/* RadioRelay.app launcher: runs run.sh as a CHILD (not exec) so macOS attributes microphone
   access to this app bundle and shows the permission prompt. Forwards SIGTERM/SIGINT. */
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static pid_t child = 0;
static void fwd(int sig) { if (child > 0) kill(child, sig); }
int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: RadioRelay <path/to/run.sh>\n"); return 64; }
    char *args[] = {"/bin/bash", argv[1], NULL};
    signal(SIGTERM, fwd); signal(SIGINT, fwd);
    if (posix_spawn(&child, "/bin/bash", NULL, NULL, args, environ) != 0) { perror("spawn"); return 1; }
    int st = 0; waitpid(child, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
