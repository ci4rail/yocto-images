"""Compile the command guards from the recovery patch and exercise wire commands."""
from pathlib import Path
import subprocess
import tempfile
import unittest

PATCH = (Path(__file__).resolve().parents[1] / 'meta-testfarm-recovery/recipes-bsp/'
         'u-boot/files/0001-testfarm-usb-recovery.patch')


class FastbootCommandTests(unittest.TestCase):
    def test_wire_commands_and_download_guard(self):
        section = PATCH.read_text().split('+++ b/drivers/fastboot/fb_command.c\n', 1)[1]
        section = section.split('--- /dev/null', 1)[0]
        patched = ''.join(line[1:] + '\n' for line in section.splitlines()
                          if line.startswith((' ', '+')))
        # Execute the actual patched parsing/guard code, before dispatch.
        guards = patched[patched.index('\tint i;'):patched.index('\tfor (i =')]
        program = r'''
#include <assert.h>
#include <string.h>
static unsigned image_size;
static void fastboot_fail(const char *message, char *response) {
 strcpy(response, message);
}
static int check(char *cmd_string, char *response) {
''' + guards + r'''
 (void)i;
 return 0;
}
static void exercise(const char *wire, unsigned size, const char *error,
                     unsigned expected_size) {
 char command[128], response[128] = "";
 strcpy(command, wire);
 image_size = size;
 assert(check(command, response) == (error ? -1 : 0));
 assert(strcmp(response, error ? error : "") == 0);
 assert(image_size == expected_size);
}
int main(void) {
 const unsigned runtime_size = 39815354;
 const char *continue_commands[] = {"continue", "continue:"};
 for (unsigned i = 0; i < 2; ++i) {
  exercise(continue_commands[i], runtime_size, NULL, runtime_size);
  exercise(continue_commands[i], 0, "No completed recovery download", 0);
 }
 exercise("getvar:version", runtime_size, NULL, runtime_size);
 exercise("download:025f88ba", runtime_size, NULL, 0);
 exercise("continue:", image_size, "No completed recovery download", 0);
 /* Even an invalid new download must invalidate the previous image. */
 exercise("download:", runtime_size, NULL, 0);
 exercise("download", runtime_size, NULL, 0);
 const char *denied[] = {"flash:boot", "erase:boot", "UCmd:reset", "ACmd:reset",
                        "boot:", "reboot:", "continue-extra:", "download-extra:1",
                        "getvar-extra:version", ":continue", ""};
 for (unsigned i = 0; i < sizeof(denied) / sizeof(denied[0]); ++i)
  exercise(denied[i], runtime_size, "Recovery command denied", runtime_size);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'commands.c'
            binary = Path(tmp) / 'commands'
            source.write_text(program)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
