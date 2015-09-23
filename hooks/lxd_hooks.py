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
    relation_get,
)

from charmhelpers.core.host import (
    umount,
)

from lxd_utils import (
    filesystem_mounted,
    determine_packages,
    install_lxd_source,
    configure_lxd_source,
    configure_lxd_block,
    lxd_trust_password,
    configure_lxd_remote,
)

from charmhelpers.fetch import (
    apt_update,
    apt_install,
    add_source,
)

hooks = Hooks()


@hooks.hook()
def install():
    log('Installing LXD')
    if config('source'):
        add_source(config('source'))
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


@hooks.hook('lxd-relation-joined',
            'lxd-migration-relation-joined')
def lxd_relation_joined(rid=None):
    settings = {}
    settings['lxd_password'] = lxd_trust_password()
    settings['lxd_hostname'] = gethostname()
    settings['lxd_address'] = unit_get('private-address')
    relation_set(relation_id=rid,
                 relation_settings=settings)


@hooks.hook('lxd-migration-relation-changed')
def lxd_migration_relation_changed():
    settings = {
        'password': relation_get('lxd_password'),
        'hostname': relation_get('lxd_hostname'),
        'address': relation_get('lxd_address'),
    }
    if all(settings):
        configure_lxd_remote(settings)


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log("Unknown hook {} - skipping.".format(e))


if __name__ == "__main__":
    main()
