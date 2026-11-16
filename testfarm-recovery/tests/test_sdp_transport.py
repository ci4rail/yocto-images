"""Exercise patched U-Boot SDP descriptors/lifecycle using a host USB stub.

Set UBOOT_SDP_SOURCE to the patched drivers/usb/gadget/f_sdp.c to run.
This checks firmware policy, not real USB controller behavior.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


def function(source, name):
    start = source.index('static ', source.index(name + '(') - 20)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


@unittest.skipUnless(os.environ.get('UBOOT_SDP_SOURCE'), 'requires patched UBOOT_SDP_SOURCE')
class SdpTransportTests(unittest.TestCase):
    def test_descriptors_and_endpoint_lifecycle(self):
        source = Path(os.environ['UBOOT_SDP_SOURCE']).read_text()
        policy = source[source.index('#ifdef CONFIG_SPL_BUILD\n#define SDP_HAS_OUT_EP'):
                        source.index('\nstruct sdp_command')]
        descriptors = source[source.index('static struct usb_interface_descriptor sdp_intf_runtime'):
                             source.index('/* This is synchronized')]
        program = STUBS + policy + descriptors
        program += '\n'.join(function(source, name) for name in
                             ('sdp_bind', 'sdp_set_alt', 'sdp_disable'))
        program += MAIN
        with tempfile.TemporaryDirectory() as tmp:
            cfile = Path(tmp) / 'sdp-test.c'
            cfile.write_text(program)
            for spl in (False, True):
                with self.subTest(spl=spl):
                    binary = Path(tmp) / ('spl' if spl else 'uboot')
                    subprocess.run(['cc', '-std=gnu11', '-Werror',
                                    *(['-DCONFIG_SPL_BUILD'] if spl else []),
                                    str(cfile), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True)


STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <errno.h>
#define USB_DT_INTERFACE 4
#define USB_CLASS_HID 3
#define USB_DT_CS_DEVICE 0x21
#define USB_DT_HID_REPORT 0x22
#define USB_DT_ENDPOINT_SIZE 7
#define USB_DT_ENDPOINT 5
#define USB_DIR_IN 0x80
#define USB_DIR_OUT 0
#define USB_ENDPOINT_XFER_INT 3
#define USB_SPEED_HIGH 3
#define HID_USAGE_PAGE_LEN 76
#define SDP_HID_PACKET_SIZE_EP1 1024
#define SDP_STATE_IDLE 0
#define __constant_cpu_to_le16(x) (x)
#define debug(...) do {} while (0)
struct usb_descriptor_header { int unused; };
struct usb_interface_descriptor {
 int bLength,bDescriptorType,bAlternateSetting,bNumEndpoints,bInterfaceClass;
 int bInterfaceSubClass,bInterfaceProtocol,bInterfaceNumber;
};
struct usb_class_hid_descriptor {
 int bLength,bDescriptorType,bcdCDC,bCountryCode,bNumDescriptors;
 int bDescriptorType0,wDescriptorLength0;
};
struct usb_endpoint_descriptor {
 int bLength,bDescriptorType,bEndpointAddress,bmAttributes,wMaxPacketSize,bInterval;
};
struct usb_ep { void *driver_data; };
struct usb_request { void *buf, *context; };
struct usb_gadget { int speed; };
struct usb_composite_dev { struct usb_gadget *gadget; struct usb_request *req; };
struct usb_configuration { struct usb_composite_dev *cdev; };
struct usb_function { struct usb_configuration *config; };
struct f_sdp {
 struct usb_function usb_function;
 struct usb_ep *in_ep, *out_ep;
 struct usb_request *in_req, *out_req;
 int altsetting, state;
 bool ep_int_enable;
};
static struct f_sdp instance;
static struct usb_ep in_endpoint, out_endpoint;
static int allocated, enabled, started, disabled, freed;
static struct f_sdp *func_to_sdp(struct usb_function *f) { return &instance; }
static int usb_interface_id(struct usb_configuration *c, struct usb_function *f) { return 0; }
static bool gadget_is_dualspeed(struct usb_gadget *g) { return true; }
static struct usb_ep *usb_ep_autoconfig(struct usb_gadget *g, struct usb_endpoint_descriptor *d) {
 allocated++;
 return (d->bEndpointAddress & USB_DIR_IN) ? &in_endpoint : &out_endpoint;
}
static int usb_ep_enable(struct usb_ep *ep, struct usb_endpoint_descriptor *d) {
 assert(ep); enabled++; return 0;
}
static struct usb_request *sdp_start_ep(struct usb_ep *ep, bool in) {
 assert(ep); started++;
 struct usb_request *req = calloc(1, sizeof(*req));
 req->buf = malloc(16); return req;
}
static void usb_ep_disable(struct usb_ep *ep) { assert(ep); disabled++; }
static void usb_ep_free_request(struct usb_ep *ep, struct usb_request *req) {
 assert(ep); freed++; free(req);
}
'''

MAIN = r'''
int main(void) {
#ifdef CONFIG_SPL_BUILD
 const int expected = 1;
#else
 const int expected = 2;
#endif
 assert(sdp_intf_runtime.bNumEndpoints == expected);
 assert(sdp_runtime_descs[2] == (void *)&in_desc);
 assert(sdp_runtime_hs_descs[2] == (void *)&in_hs_desc);
 assert(sdp_runtime_descs[expected + 2] == NULL);
 assert(sdp_runtime_hs_descs[expected + 2] == NULL);
 if (expected == 2) {
  assert(sdp_runtime_descs[3] == (void *)&out_desc);
  assert(sdp_runtime_hs_descs[3] == (void *)&out_hs_desc);
 }
 for (int high = 0; high < 2; high++) {
  instance = (struct f_sdp){0};
  allocated = enabled = started = disabled = freed = 0;
  struct usb_gadget gadget = { .speed = high ? USB_SPEED_HIGH : 1 };
  struct usb_request req = {0};
  struct usb_composite_dev cdev = { &gadget, &req };
  struct usb_configuration config = { &cdev };
  instance.usb_function.config = &config;
  assert(sdp_bind(&config, &instance.usb_function) == 0);
  assert(allocated == expected);
  assert(sdp_set_alt(&instance.usb_function, 0, 0) == 0);
  assert(enabled == expected && started == expected);
  assert(instance.ep_int_enable == (expected == 2));
  assert((instance.out_ep != NULL) == (expected == 2));
  assert((instance.out_req != NULL) == (expected == 2));
  sdp_disable(&instance.usb_function);
  assert(disabled == expected && freed == expected);
 }
 return 0;
}
'''
