/* Serve the embedded TEZI image on localhost or TEZI's USB gadget address.
 * The image filenames are validated by build.py before packaging.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static int write_all(int fd, const char *data, size_t size)
{
    while (size) {
        ssize_t n = write(fd, data, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        data += n;
        size -= (size_t)n;
    }
    return 0;
}

static void error_response(int client, int code, const char *reason)
{
    char header[256];
    int n = snprintf(header, sizeof(header),
        "HTTP/1.1 %d %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        code, reason);
    if (n > 0 && (size_t)n < sizeof(header)) write_all(client, header, (size_t)n);
}

static int allowed_name(const char *name)
{
    if (!*name || strlen(name) > 255 || !strcmp(name, ".") || !strcmp(name, ".."))
        return 0;
    for (const unsigned char *p = (const unsigned char *)name; *p; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || strchr("_+.,=-", *p)))
            return 0;
    return 1;
}

static void serve(int client, int root)
{
    char request[4096], method[8], path[300], version[16];
    size_t used = 0;
    while (used < sizeof(request) - 1) {
        ssize_t n = read(client, request + used, sizeof(request) - 1 - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return;
        used += (size_t)n;
        request[used] = 0;
        if (strstr(request, "\r\n\r\n") || strstr(request, "\n\n")) break;
    }
    if (sscanf(request, "%7s %299s %15s", method, path, version) != 3 ||
        (strcmp(method, "GET") && strcmp(method, "HEAD")) ||
        strncmp(version, "HTTP/1.", 7) || path[0] != '/' ||
        !allowed_name(path + 1)) {
        error_response(client, 400, "Bad Request");
        return;
    }
    int file = openat(root, path + 1, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (file < 0) {
        error_response(client, 404, "Not Found");
        return;
    }
    struct stat st;
    if (fstat(file, &st) || !S_ISREG(st.st_mode) || st.st_size < 0) {
        close(file);
        error_response(client, 404, "Not Found");
        return;
    }
    off_t offset = 0;
    int partial = 0;
    const char *range = strcasestr(request, "\nRange:");
    if (range) {
        const char *value = range + 7;
        while (*value == ' ') ++value;
        if (strncmp(value, "bytes=", 6)) {
            close(file);
            error_response(client, 416, "Range Not Satisfiable");
            return;
        }
        value += 6;
        errno = 0;
        char *range_end;
        long long start = strtoll(value, &range_end, 10);
        if (errno || range_end == value || start < 0 || start >= st.st_size ||
            *range_end != '-' || (range_end[1] != '\r' && range_end[1] != '\n')) {
            close(file);
            error_response(client, 416, "Range Not Satisfiable");
            return;
        }
        offset = (off_t)start;
        partial = 1;
    }
    char header[512];
    const char *type = strstr(path, ".json") ? "application/json" : "application/octet-stream";
    int n;
    if (partial)
        n = snprintf(header, sizeof(header),
            "HTTP/1.1 206 Partial Content\r\nContent-Length: %lld\r\n"
            "Content-Range: bytes %lld-%lld/%lld\r\nContent-Type: %s\r\n"
            "Connection: close\r\n\r\n", (long long)(st.st_size - offset),
            (long long)offset, (long long)(st.st_size - 1), (long long)st.st_size, type);
    else
        n = snprintf(header, sizeof(header),
            "HTTP/1.1 200 OK\r\nContent-Length: %lld\r\nContent-Type: %s\r\n"
            "Connection: close\r\n\r\n", (long long)st.st_size, type);
    if (n > 0 && (size_t)n < sizeof(header) && !write_all(client, header, (size_t)n) &&
        !strcmp(method, "GET")) {
        while (offset < st.st_size) {
            ssize_t sent = sendfile(client, file, &offset, (size_t)(st.st_size - offset));
            if (sent < 0 && errno == EINTR) continue;
            if (sent <= 0) break;
        }
    }
    close(file);
}

int main(int argc, char **argv)
{
    if (argc != 4) {
        fprintf(stderr, "usage: local_http DIRECTORY ADDRESS PORT\n");
        return 2;
    }
    char *end;
    long port = strtol(argv[3], &end, 10);
    if (*end || port < 1024 || port > 65535) return 2;
    int root = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (root < 0) { perror("open image directory"); return 1; }
    int server = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (server < 0) { perror("socket"); return 1; }
    int one = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    if (strcmp(argv[2], "127.0.0.1") && strcmp(argv[2], "192.168.11.1")) return 2;
    if (inet_pton(AF_INET, argv[2], &address.sin_addr) != 1) return 2;
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) || listen(server, 8)) {
        perror("bind/listen"); return 1;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGCHLD, SIG_IGN);
    for (;;) {
        int client = accept(server, NULL, NULL);
        if (client < 0) { if (errno == EINTR) continue; perror("accept"); return 1; }
        pid_t child = fork();
        if (child == 0) {
            close(server);
            serve(client, root);
            close(client);
            _exit(0);
        }
        close(client);
        if (child < 0) { perror("fork"); return 1; }
    }
}
