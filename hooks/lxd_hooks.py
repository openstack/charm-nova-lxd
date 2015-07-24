#!/usr/bin/env python

from socket import gethostname
from subprocess import check_call
import sys

from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    log,
    unit_get,
    relation_set,
    relation_ids,
)

from charmhelpers.core.host import (
    adduser,
    mkdir,
    restart_on_change,
    service_start
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
    relation_set(relation_id=rid,
                 lxd_hostname=unit_get('private-address'),
                 lxd_address=gethostname(),
                 lxd_password=config('lxd-trust-password'))

def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log("Unknown hook {} - skipping.".format(e))

if __name__ == "__main__":
    main()
