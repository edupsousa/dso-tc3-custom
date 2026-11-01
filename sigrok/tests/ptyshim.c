/* LD_PRELOAD shim for tests: libserialport needs the modem-control ioctls, which a pty refuses with ENOTTY.
 * Answers them with "all lines low" so the fake device (fake_tc3.py) on a pty can be opened. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <sys/ioctl.h>

int ioctl(int fd, unsigned long req, ...)
{
	static int (*real)(int, unsigned long, ...);
	va_list ap;
	void *arg;
	int r;

	if (!real)
		real = dlsym(RTLD_NEXT, "ioctl");
	va_start(ap, req);
	arg = va_arg(ap, void *);
	va_end(ap);
	r = real(fd, req, arg);
	if (r < 0 && errno == ENOTTY) {
		if (req == TIOCMGET) {
			*(int *)arg = 0;
			return 0;
		}
		if (req == TIOCMSET || req == TIOCMBIS || req == TIOCMBIC)
			return 0;
	}
	return r;
}
