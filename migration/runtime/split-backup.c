/* Split stdin into FAT32-safe archive chunks. No external split in TEZI. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#ifndef BACKUP_CHUNK_BYTES
#define BACKUP_CHUNK_BYTES (UINT64_C(1024) * 1024 * 1024)
#endif

int main(int argc, char **argv)
{
    unsigned char buffer[65536];
    const uint64_t limit = BACKUP_CHUNK_BYTES;
    uint64_t written = 0;
    unsigned int part = 0;
    FILE *out = NULL;
    size_t count;
    if (argc != 2) return 2;
    while ((count = fread(buffer, 1, sizeof(buffer), stdin)) != 0) {
        if (!out) {
            char *name = malloc(strlen(argv[1]) + 16);
            if (!name || part > 9999) return 1;
            sprintf(name, "%s%04u", argv[1], part++);
            out = fopen(name, "wx");
            free(name);
            if (!out) { perror("backup chunk"); return 1; }
            written = 0;
        }
        if (fwrite(buffer, 1, count, out) != count) {
            perror("backup write"); fclose(out); return 1;
        }
        written += count;
        if (written == limit) {
            if (fclose(out)) { perror("backup close"); return 1; }
            out = NULL;
        }
    }
    if (ferror(stdin)) { perror("backup read"); if (out) fclose(out); return 1; }
    if (out && fclose(out)) { perror("backup close"); return 1; }
    return 0;
}
