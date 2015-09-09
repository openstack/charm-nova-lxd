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

from lxd_utils import (
    install_lxd,
)

hooks = Hooks()


@hooks.hook()
def install():
    log('Instatlling LXD')
    install_lxd()


@hooks.hook('lxd-relation-joined')
def relation_joined(rid=None):
    settings = {}

    settings['lxd_password'] = config('lxd-trust-password')
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
