#!/usr/bin/env python

from socket import gethostname
import sys

from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    log,
    unit_get,
    relation_set,
)

from charmhelpers.core.host import (
    umount,
    service_restart,
)

from lxd_utils import (
    filesystem_mounted,
    determine_packages,
    install_lxd_source,
    configure_lxd_source,
    configure_lxd_block,
)

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source
)

from charmhelpers.fetch import (
    apt_update,
    apt_install,
)

hooks = Hooks()


@hooks.hook()
def install():
    log('Installing LXD')
    if config('source'):
        configure_installation_source(config('source'))
    apt_update(fatal=True)
    apt_install(determine_packages(), fatal=True)
    if config('use-source'):
        install_lxd_source()
        configure_lxd_source()


@hooks.hook()
def config_changed():
    e_mountpoint = config('ephemeral-unmount')
    if e_mountpoint and filesystem_mounted(e_mountpoint):
        umount(e_mountpoint)

    configure_lxd_block()
    service_restart('lxd')


@hooks.hook('lxd-relation-joined')
def relation_joined(rid=None):
    settings = {}
    settings['lxd_password'] = config('trust-password')
    settings['lxd_hostname'] = unit_get('private-address')
    settings['lxd_address'] = gethostname()
    relation_set(relation_id=rid,
                 relation_settings=settings)


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log("Unknown hook {} - skipping.".format(e))


if __name__ == "__main__":
    main()
