/*
 * nosync.c - a minimal libeatmydata for glibc/Linux.
 *
 * gcc -O2 -shared -fPIC -o libnosync.so nosync.c -ldl
 */

#define _GNU_SOURCE
/*
 * Force the plain (non-LFS-redirected) prototypes so that open()/openat() and
 * open64()/openat64() stay distinct symbols. Without this, building with
 * -D_FILE_OFFSET_BITS=64 (injected by some distro CFLAGS/GCC specs) redirects
 * our open() definition to open64() and collides with the explicit one.
 */
#undef _FILE_OFFSET_BITS
#undef __USE_FILE_OFFSET64
#include <sys/types.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdint.h>

void sync(void) { }
int fsync(int fd) { (void)fd; return 0; }
int fdatasync(int fd) { (void)fd; return 0; }
int syncfs(int fd) { (void)fd; return 0; }
int msync(void *addr, size_t length, int flags)
{
    (void)addr; (void)length; (void)flags;
    return 0;
}
int sync_file_range(int fd, off64_t offset, off64_t nbytes, unsigned int flags)
{
    (void)fd; (void)offset; (void)nbytes; (void)flags;
    return 0;
}

/*
 * On Linux O_SYNC already includes the O_DSYNC bit and O_RSYNC == O_SYNC, so
 * clearing (O_SYNC | O_DSYNC) removes all synchronous semantics.
 *
 * The 64-bit variants matter: code built with -D_FILE_OFFSET_BITS=64 (common)
 * calls open64()/openat64() instead of open()/openat().
 *
 * The variadic mode is read unconditionally; the kernel ignores it unless
 * O_CREAT/O_TMPFILE is set, so this is safe.
 */
#define EAT_OPEN(fn) \
    int fn(const char *pathname, int flags, ...) \
    { \
        static int (*real)(const char *, int, ...); \
        va_list ap; \
        mode_t mode; \
        va_start(ap, flags); \
        mode = va_arg(ap, mode_t); \
        va_end(ap); \
        if (!real) \
            real = (int (*)(const char *, int, ...)) \
                   (intptr_t)dlsym(RTLD_NEXT, #fn); \
        return real(pathname, flags & ~(O_SYNC | O_DSYNC), mode); \
    }

#define EAT_OPENAT(fn) \
    int fn(int dirfd, const char *pathname, int flags, ...) \
    { \
        static int (*real)(int, const char *, int, ...); \
        va_list ap; \
        mode_t mode; \
        va_start(ap, flags); \
        mode = va_arg(ap, mode_t); \
        va_end(ap); \
        if (!real) \
            real = (int (*)(int, const char *, int, ...)) \
                   (intptr_t)dlsym(RTLD_NEXT, #fn); \
        return real(dirfd, pathname, \
                flags & ~(O_SYNC | O_DSYNC), mode); \
    }

EAT_OPEN(open)
EAT_OPEN(open64)
EAT_OPENAT(openat)
EAT_OPENAT(openat64)
