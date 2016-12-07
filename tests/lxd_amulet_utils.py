# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Temporary Local Helpers - Extends OpenStackAmuletUtils
# ============================================================================
# NOTE:
# Move to charmhelpers/contrib/openstack/amulet/utils.py once
# validated and settled.
#
# These helpers are and should be written in a way that they
# are not LXD-specific.  They should default to KVM/x86_64
# with enough parameters plumbed to allow LXD.
#

import amulet
import logging
import os
import urllib

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils
)

from novaclient import exceptions

DEBUG = logging.DEBUG
ERROR = logging.ERROR

# LXD_IMAGE_URL = 'http://cloud-images.ubuntu.com/trusty/current/trusty-server-cloudimg-amd64-root.tar.xz'  # noqa


class LXDAmuletUtils(OpenStackAmuletUtils):
    """LXD amulet utilities.

       This class inherits from AmuletUtils and has additional support
       that is specifically for use by OpenStack charm tests.
       """

    def __init__(self, log_level=ERROR):
        """Initialize the deployment environment."""
        super(LXDAmuletUtils, self).__init__(log_level)

    # NOTE(beisner):  to eventually replace the existing amulet openstack
    # glance image creation helper method.  Plopped here to fine-tune and
    # make more flexible.
    def glance_create_image(self, glance, image_name, image_url,
                            download_dir='tests',
                            hypervisor_type='qemu',
                            disk_format='qcow2',
                            architecture='x86_64',
                            container_format='bare'):
        """Download an image and upload it to glance, validate its status
        and return an image object pointer. KVM defaults, can override for
        LXD.

        :param glance: pointer to authenticated glance api connection
        :param image_name: display name for new image
        :param image_url: url to retrieve
        :param download_dir: directory to store downloaded image file
        :param hypervisor_type: glance image hypervisor property
        :param disk_format: glance image disk format
        :param architecture: glance image architecture property
        :param container_format: glance image container format
        :returns: glance image pointer
        """
        self.log.debug('Creating glance image ({}) from '
                       '{}...'.format(image_name, image_url))

        # Download image
        http_proxy = os.getenv('AMULET_HTTP_PROXY')
        self.log.debug('AMULET_HTTP_PROXY: {}'.format(http_proxy))
        if http_proxy:
            proxies = {'http': http_proxy}
            opener = urllib.FancyURLopener(proxies)
        else:
            opener = urllib.FancyURLopener()

        abs_file_name = os.path.join(download_dir, image_name)
        if not os.path.exists(abs_file_name):
            opener.retrieve(image_url, abs_file_name)

        # Create glance image
        glance_properties = {
            'architecture': architecture,
            'hypervisor_type': hypervisor_type
        }
        with open(abs_file_name) as f:
            image = glance.images.create(name=image_name,
                                         is_public=True,
                                         disk_format=disk_format,
                                         container_format=container_format,
                                         properties=glance_properties,
                                         data=f)

        # Wait for image to reach active status
        img_id = image.id
        ret = self.resource_reaches_status(glance.images, img_id,
                                           expected_stat='active',
                                           msg='Image status wait')
        if not ret:
            msg = 'Glance image failed to reach expected state.'
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Re-validate new image
        self.log.debug('Validating image attributes...')
        val_img_name = glance.images.get(img_id).name
        val_img_stat = glance.images.get(img_id).status
        val_img_pub = glance.images.get(img_id).is_public
        val_img_cfmt = glance.images.get(img_id).container_format
        val_img_dfmt = glance.images.get(img_id).disk_format
        msg_attr = ('Image attributes - name:{} public:{} id:{} stat:{} '
                    'container fmt:{} disk fmt:{}'.format(
                        val_img_name, val_img_pub, img_id,
                        val_img_stat, val_img_cfmt, val_img_dfmt))

        if val_img_name == image_name and val_img_stat == 'active' \
                and val_img_pub is True and val_img_cfmt == container_format \
                and val_img_dfmt == disk_format:
            self.log.debug(msg_attr)
        else:
            msg = ('Image validation failed, {}'.format(msg_attr))
            amulet.raise_status(amulet.FAIL, msg=msg)

        return image

    def create_flavor(self, nova, name, ram, vcpus, disk, flavorid="auto",
                      ephemeral=0, swap=0, rxtx_factor=1.0, is_public=True):
        """Create the specified flavor."""
        try:
            nova.flavors.find(name=name)
        except (exceptions.NotFound, exceptions.NoUniqueMatch):
            self.log.debug('Creating flavor ({})'.format(name))
            nova.flavors.create(name, ram, vcpus, disk, flavorid,
                                ephemeral, swap, rxtx_factor, is_public)
