import glob
import pwd
import os

from subprocess import call, check_call, check_output, CalledProcessError

from charmhelpers.core.templating import render
from charmhelpers.core.hookenv import (
    log,
    config,
    ERROR,
    INFO,
    status_set,
)
from charmhelpers.core.unitdata import kv
from charmhelpers.core.host import (
    add_group,
    add_user_to_group,
    mkdir,
    mount,
    mounts,
    umount,
    service_stop,
    service_start,
    pwgen,
    lsb_release,
)
from charmhelpers.contrib.storage.linux.utils import (
    is_block_device,
    zap_disk,
)
from charmhelpers.contrib.storage.linux.loopback import (
    ensure_loopback_device
)
from charmhelpers.contrib.storage.linux.lvm import (
    create_lvm_volume_group,
    create_lvm_physical_volume,
    list_lvm_volume_group,
    is_lvm_physical_volume,
    deactivate_lvm_volume_group,
    remove_lvm_physical_volume,
)
from charmhelpers.core.decorators import retry_on_exception

BASE_PACKAGES = [
    'btrfs-tools',
    'lvm2',
    'thin-provisioning-tools'
]
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
PW_LENGTH = 16


def install_lxd():
    '''Install LXD'''


def install_lxd_source(user='ubuntu'):
    '''Install LXD from source repositories; installs toolchain first'''
    log('Installing LXD from source')

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
    '''Add required configuration and files when deploying LXD from source'''
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

    service_stop('lxd')
    files = glob.glob('%s/bin/*' % GOPATH)
    for i in files:
        cmd = ['cp', i, '/usr/bin']
        check_call(cmd)
    service_start('lxd')


def configure_lxd_block():
    '''Configure a block device for use by LXD for containers'''
    log('Configuring LXD container storage')
    if filesystem_mounted('/var/lib/lxd'):
        log('/varlib/lxd already configured, skipping')
        return

    lxd_block_device = config('block-device')
    if not lxd_block_device:
        log('block device is not provided - skipping')
        return

    dev = None
    if lxd_block_device.startswith('/dev/'):
        dev = lxd_block_device
    elif lxd_block_device.startswith('/'):
        log('Configuring loopback device for use with LXD')
        _bd = lxd_block_device.split('|')
        if len(_bd) == 2:
            dev, size = _bd
        else:
            dev = lxd_block_device
            size = DEFAULT_LOOPBACK_SIZE
        dev = ensure_loopback_device(dev, size)

    if not dev or not is_block_device(dev):
        log('Invalid block device provided: %s' % lxd_block_device)
        return

    # NOTE: check overwrite and ensure its only execute once.
    db = kv()
    if config('overwrite') and not db.get('scrubbed', False):
        clean_storage(dev)
        db.set('scrubbed', True)
        db.flush()

    if not os.path.exists('/var/lib/lxd'):
        mkdir('/var/lib/lxd')

    if config('storage-type') == 'btrfs':
        status_set('maintenance',
                   'Configuring btrfs container storage')
        service_stop('lxd')
        cmd = ['mkfs.btrfs', '-f', dev]
        check_call(cmd)
        mount(dev,
              '/var/lib/lxd',
              options='user_subvol_rm_allowed',
              persist=True,
              filesystem='btrfs')
        service_start('lxd')
    elif config('storage-type') == 'lvm':
        if (is_lvm_physical_volume(dev) and
                list_lvm_volume_group(dev) == 'lxd_vg'):
            log('Device already configured for LVM/LXD, skipping')
            return
        status_set('maintenance',
                   'Configuring LVM container storage')
        # Enable and startup lvm2-lvmetad to avoid extra output
        # in lvm2 commands, which confused lxd.
        cmd = ['systemctl', 'enable', 'lvm2-lvmetad']
        check_call(cmd)
        cmd = ['systemctl', 'start', 'lvm2-lvmetad']
        check_call(cmd)
        create_lvm_physical_volume(dev)
        create_lvm_volume_group('lxd_vg', dev)
        cmd = ['lxc', 'config', 'set', 'storage.lvm_vg_name', 'lxd_vg']
        check_call(cmd)


def determine_packages():
    packages = [] + BASE_PACKAGES
    packages = list(set(packages))
    if config('use-source'):
        packages.extend(LXD_SOURCE_PACKAGES)
    else:
        packages.extend(LXD_PACKAGES)
    return packages


def filesystem_mounted(fs):
    return call(['grep', '-wqs', fs, '/proc/mounts']) == 0


def lxd_trust_password():
    db = kv()
    password = db.get('lxd-password')
    if not password:
        password = db.set('lxd-password', pwgen(PW_LENGTH))
        db.flush()
    return password


def configure_lxd_remote(settings, user='root'):
    cmd = ['sudo', '-u', user,
           'lxc', 'remote', 'list']
    output = check_output(cmd)
    if settings['hostname'] not in output:
        log('Adding new remote {hostname}:{address}'.format(**settings))
        cmd = ['sudo', '-u', user,
               'lxc', 'remote', 'add',
               settings['hostname'],
               'https://{}:8443'.format(settings['address']),
               '--accept-certificate',
               '--password={}'.format(settings['password'])]
        check_call(cmd)
    else:
        log('Updating remote {hostname}:{address}'.format(**settings))
        cmd = ['sudo', '-u', user,
               'lxc', 'remote', 'set-url',
               settings['hostname'],
               'https://{}:8443'.format(settings['address'])]
        check_call(cmd)


@retry_on_exception(5, base_delay=2, exc_type=CalledProcessError)
def configure_lxd_host():
    ubuntu_release = lsb_release()['DISTRIB_CODENAME'].lower()
    if ubuntu_release > "vivid":
        log('>= Wily deployment - configuring LXD trust password and address',
            level=INFO)
        cmd = ['lxc', 'config', 'set',
               'core.trust_password', lxd_trust_password()]
        check_call(cmd)
        cmd = ['lxc', 'config', 'set',
               'core.https_address', '[::]']
        check_call(cmd)
    elif ubuntu_release == "vivid":
        log('Vivid deployment - loading overlay kernel module', level=INFO)
        cmd = ['modprobe', 'overlay']
        check_call(cmd)
        with open('/etc/modules', 'r+') as modules:
            if 'overlay' not in modules.read():
                modules.write('overlay')


def clean_storage(block_device):
    '''Ensures a block device is clean.  That is:
        - unmounted
        - any lvm volume groups are deactivated
        - any lvm physical device signatures removed
        - partition table wiped

    :param block_device: str: Full path to block device to clean.
    '''
    for mp, d in mounts():
        if d == block_device:
            log('clean_storage(): Found %s mounted @ %s, unmounting.' %
                (d, mp))
            umount(mp, persist=True)

    if is_lvm_physical_volume(block_device):
        deactivate_lvm_volume_group(block_device)
        remove_lvm_physical_volume(block_device)

    zap_disk(block_device)


def lxd_running():
    '''Check whether LXD is running or not'''
    cmd = ['pgrep', 'lxd']
    try:
        check_call(cmd)
        return True
    except CalledProcessError:
        return False


def assess_status():
    '''Determine status of current unit'''
    if lxd_running():
        status_set('active', 'Unit is ready')
    else:
        status_set('blocked', 'LXD is not running')
