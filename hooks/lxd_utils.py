import glob
import pwd
import re
import os

from subprocess import call, check_call

from charmhelpers.core.templating import render

from charmhelpers.core.hookenv import (
    log,
    config,
    ERROR,
)

from charmhelpers.core.host import (
    add_group,
    add_user_to_group,
    mkdir,
    service_restart,
    service_stop,
    mount,
)

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source
)

from charmhelpers.contrib.storage.linux.utils import (
    is_block_device,
)
from charmhelpers.contrib.storage.linux.loopback import (
    ensure_loopback_device
)
from charmhelpers.contrib.storage.linux.lvm import (
    create_lvm_volume_group,
    create_lvm_physical_volume
)


from charmhelpers.fetch import (
    apt_update,
    apt_install,
)

BASE_PACKAGES = ['btrfs-tools', 'lvm2']
LXD_PACKAGES = ['lxd', 'lxd-client']
LXD_SOURCE_PACKAGES = [
    'lxc',
    'lxc-dev',
    'mercurial',
    'git',
    'pkg-config',
    'protobuf-compiler',
    'golang-goprotobuf-dev',
    'build-essential',
    'golang',
    'xz-utils',
    'tar',
    'acl',
]

LXD_GIT = 'github.com/lxc/lxd'
DEFAULT_LOOPBACK_SIZE = '10G'


def install_lxd():
    configure_installation_source(config('lxd-origin'))
    apt_update(fatal=True)
    apt_install(determine_packages(), fatal=True)

    if config('lxd-use-source'):
        install_lxd_source()
        configure_lxd_source()
    else:
        service_stop('lxd')

    configure_lxd_block()
    service_restart('lxd')


def install_lxd_source(user='ubuntu'):
    log('Installing LXD Source')

    home = pwd.getpwnam(user).pw_dir
    GOPATH = os.path.join(home, 'go')
    LXD_SRC = os.path.join(GOPATH, 'src', 'github.com/lxc/lxd')

    if not os.path.exists(GOPATH):
        mkdir(GOPATH)

    env = os.environ.copy()
    env['GOPATH'] = GOPATH
    env['HTTP_PROXY'] = 'http://squid.internal:3128'
    env['HTTPS_PROXY'] = 'https://squid.internal:3128'
    cmd = 'go get -v %s' % LXD_GIT
    log('Installing LXD: %s' % (cmd))
    check_call(cmd, env=env, shell=True)

    if not os.path.exists(LXD_SRC):
        log('Failed to go get %s' % LXD_GIT, level=ERROR)
        raise

    cwd = os.getcwd()
    try:
        os.chdir(LXD_SRC)
        cmd = 'go get -v -d ./...'
        log('Downloading LXD deps: %s' % (cmd))
        call(cmd, env=env, shell=True)

        # build deps
        cmd = 'make'
        log('Building LXD deps: %s' % (cmd))
        call(cmd, env=env, shell=True)
    except Exception:
        log("failed to install lxd")
        raise
    finally:
        os.chdir(cwd)


def configure_lxd_source(user='ubuntu'):
    log('Configuring LXD Source')
    home = pwd.getpwnam(user).pw_dir
    GOPATH = os.path.join(home, 'go')

    templates_dir = 'templates'
    render('lxd_upstart', '/etc/init/lxd.conf', {},
           perms=0o644, templates_dir=templates_dir)
    render('lxd_service', '/lib/systemd/system/lxd.service', {},
           perms=0o644, templates_dir=templates_dir)
    add_group('lxd', system_group=True)
    add_user_to_group(user, 'lxd')

    files = glob.glob('%s/bin/*' % GOPATH)
    for i in files:
        cmd = ['cp', i, '/usr/bin']
        check_call(cmd)


def configure_lxd_block():
    log('Configuring LXD block device')
    lxd_block_device = config('lxd-block-device')
    if not lxd_block_device:
        log('btrfs device is not specified')
        return

    if not os.path.exists('/var/lib/lxd'):
        mkdir('/var/lib/lxd')

    if config('lxd-fs-type') == 'btrfs':
        for dev in determine_block_devices():
            cmd = ['mkfs.btrfs', '-f', dev]
            check_call(cmd)
            mount(dev,
                  '/var/lib/lxd',
                  options='user_subvol_rm_allowed',
                  persist=True,
                  filesystem='btrfs')
    elif config('lxd-fs-type') == 'lvm':
        devices = determine_block_devices()
        if devices:
            for dev in devices:
                create_lvm_physical_volume(dev)
                create_lvm_volume_group('lxd_vg', dev)


def find_block_devices():
    found = []
    incl = ['sd[a-z]', 'vd[a-z]', 'cciss\/c[0-9]d[0-9]']
    blacklist = ['sda', 'vda', 'cciss/c0d0']
    with open('/proc/partitions') as proc:
        print proc
        partitions = [p.split() for p in proc.readlines()[2:]]
    for partition in [p[3] for p in partitions if p]:
        for inc in incl:
            _re = re.compile(r'^(%s)$' % inc)
            if _re.match(partition) and partition not in blacklist:
                found.append(os.path.join('/dev', partition))
    return [f for f in found if is_block_device(f)]


def determine_block_devices():
    block_device = config('lxd-block-device')

    if not block_device or block_device in ['None', 'none']:
        log('No storage deivces specified in config as block-device',
            level=ERROR)
        return None

    if block_device == 'guess':
        bdevs = find_block_devices()
    else:
        bdevs = block_device.split(' ')
    # attemps to ensure block devices, but filter out missing devs
    _none = ['None', 'none', None]
    valid_bdevs = \
        [x for x in map(ensure_block_device, bdevs) if x not in _none]
    log('Valid ensured block devices: %s' % valid_bdevs)
    return valid_bdevs


def ensure_block_device(block_device):
    _none = ['None', 'none', None]
    if (block_device in _none):
        log('prepare_storage(): Missing required input: '
            'block_device=%s.' % block_device, level=ERROR)
        raise

    if block_device.startswith('/dev/'):
        bdev = block_device
    elif block_device.startswith('/'):
        _bd = block_device.split('|')
        if len(_bd) == 2:
            bdev, size = _bd
        else:
            bdev = block_device
            size = DEFAULT_LOOPBACK_SIZE
        bdev = ensure_loopback_device(bdev, size)
    else:
        bdev = '/dev/%s' % block_device

    if not is_block_device(bdev):
        log('Failed to locate valid block device at %s' % bdev, level=ERROR)
        return

    return bdev


def determine_packages():
    packages = [] + BASE_PACKAGES
    packages = list(set(packages))
    if config('lxd-use-source'):
        packages.extend(LXD_SOURCE_PACKAGES)
    else:
        packages.extend(LXD_PACKAGES)
    return packages
