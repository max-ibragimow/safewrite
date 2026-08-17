# safewrite

Atomic file writes with no dependencies: the content is replaced in full, or not replaced
at all. Neither a crashed process, nor `Ctrl-C`, nor a full disk will leave you with a
truncated config file.

**Python 3.8+ · no dependencies · Linux, macOS, Windows**

```console
$ pip install safewrite
```

## Why

`open(path, "w")` truncates the file the moment it is opened — the old data is already
gone while the new data has not been written yet:

```python
with open("config.json", "w") as f:
    json.dump(config, f)   # crash here -> an empty file on disk
```

`safewrite` writes to a temporary file in the same directory and swaps it into place with
a single `os.replace()`. Readers see either the old content or the new one, never anything
in between.

```python
from safewrite import atomic_write

with atomic_write("config.json", encoding="utf-8") as f:
    json.dump(config, f)   # crash here -> the old file is intact, the temp file is gone
```

Shorthands for data you already have:

```python
from safewrite import write_bytes, write_text

write_text("state.json", json.dumps(state), encoding="utf-8")
write_bytes("model.bin", payload)
write_text("service.token", token, perms=0o600)   # never world-readable, not even briefly
```

## Isn't this 20 lines of my own code?

The first 20 lines are easy: `mkstemp`, write, `os.replace`. The next ones are the reason
this package exists — and each of them is a real incident someone has already had:

* an existing file silently loses its mode, because `mkstemp` creates files as `0600`;
* a new file gets `0600` instead of what your `umask` says;
* a secret exists on disk world-readable for a moment, because `chmod` runs after the write;
* the data survives the crash but not the power loss, because the directory was never `fsync`ed;
* a symlinked config (`/etc/app.conf -> /mnt/conf/app.conf`) turns into a regular file;
* `Ctrl-C` in the middle leaves a `.app.conf.x7f2.tmp` next to the real file, forever;
* `overwrite=False` implemented as "check, then write" races with another process.

All seven are handled here and covered by tests.

## What is preserved, and what is not

| | Preserved |
|---|---|
| File content | replaced atomically, all or nothing |
| Permissions of an existing file | yes — `0640` stays `0640` |
| Permissions of a new file | `0o666 & ~umask`, like a plain `open()`; `umask` is re-read on every write |
| Explicit `perms=0o600` | applied before the first byte is written (POSIX; on Windows `chmod` only toggles the read-only flag) |
| setuid / setgid / sticky | yes — restored after the swap, since the kernel clears them on write |
| Symlinks | followed by default — the link stays a link, its target is rewritten |
| Owner (uid/gid) | **no** — a new file belongs to whoever wrote it; running as root makes the file root's |
| The inode | **no** — readers holding the file open keep seeing the old content forever |
| POSIX ACLs | **no** — reset to the basic mode bits |
| Extended attributes (xattr) | **no** — dropped |
| SELinux context | **no** — inherited from the directory; run `restorecon` if the file had a custom label |
| Hard links | **no** — the link is broken, the other name keeps the old content |

Every "no" row follows from the same fact: the result is a new inode, not the old file
modified in place. If you need one of them, write in place and accept the risk, or restore
the attribute yourself after the swap.

## Durability

`fsync` on the file before the swap and on the directory after it, so a completed write
survives a power loss. This is not free. Writing 300 small JSON files on a consumer NVMe (ext4); spinning disks and NFS are slower still:

| | per file | 300 files |
|---|---|---|
| `durable=True` (default) | 12.2 ms | 3.66 s |
| `durable=False` | 0.05 ms | 0.01 s |
| plain `open()` | 0.02 ms | 0.01 s |

Two orders of magnitude — the cost is the flush, not the library.

When writing hundreds of files in a batch, `durable=False` plus a single `os.sync()` at
the end trades a narrow window of risk for two orders of magnitude of speed. (`os.sync()`
is Unix-only, and until it runs the batch is exposed to the usual delayed-allocation
surprise — files that exist but are empty after a power cut.)

## CLI

Installing the package adds a `safewrite` command — a `sponge` workalike from moreutils:
stdin is read to the end, and only then the file is swapped.

```console
$ grep -v DEBUG app.log | safewrite app.log     # a plain `> app.log` would truncate it
$ curl -s https://example.com/data | safewrite data.json --no-clobber
$ vault read -field=token secret/app | safewrite app.token --perms 600
```

**Do not filter a log a running service still holds open.** The swap gives the path a new
inode, and the writer keeps its file descriptor on the old one — everything it logs after
that goes nowhere until it reopens the file. This is not specific to `safewrite`, it is
what any replace-based rewrite does, `sponge` included. Rotate the log properly
(`logrotate` with `copytruncate`) or reload the service afterwards.

`--append` reads the whole file back and rewrites it, which is neither cheap on large
files nor atomic against other appenders — 40 concurrent `--append` runs lose lines,
40 concurrent `>>` do not. Use the shell's `>>` for that; `--append` is for the case where
nothing else is writing.

If the command on the left of the pipe fails, stdin is empty — and a naive sponge would
wipe your log. `safewrite` refuses to truncate a non-empty file with empty input:

```console
$ grep -v DEBUG missing.log | safewrite app.log
safewrite: refusing to truncate 'app.log' with empty input (the command upstream may
have failed; pass --allow-empty to force)
$ echo -n "" | safewrite app.log --allow-empty    # deliberate truncation
```

Exit codes: `0` written, `2` write failed, `3` refused to truncate. `--no-fsync` is the
CLI spelling of `durable=False`.

## Offline and air-gapped installs

Pure Python, zero dependencies, one universal `py3-none-any` wheel — nothing is compiled
at install time and no build backend is needed:

```console
$ pip download safewrite -d ./wheels          # on a connected host
$ pip install --no-index --find-links ./wheels safewrite   # inside the closed network
```

## Concurrency

Two overlapping writers leave you with the full content of one of them, never a mix — no
locking required for that guarantee. Locking *is* required for read-modify-write cycles,
where a lost update is still possible: use `flock(1)` around the CLI, or `fcntl.flock` on
a separate lock file in Python.

## Other limitations

* Atomicity comes from `os.replace()`, which works within a single filesystem. The
  temporary file is always created next to the resolved target, so this holds even when
  the path is a symlink pointing to another mount.
* On Windows the directory `fsync` is unavailable and is skipped; `os.replace()` fails if
  the file is open in another process.
* The directory must be writable — the temporary file is created there, not in `/tmp`.

## API

```python
atomic_write(path, mode="w", *, encoding=None, errors=None, newline=None,
             perms=None, overwrite=True, durable=True, follow_symlinks=True)
write_text(path, data, *, encoding="utf-8", errors=None, newline=None,
           perms=None, overwrite=True, durable=True, follow_symlinks=True)
write_bytes(path, data, *, perms=None, overwrite=True, durable=True, follow_symlinks=True)
```

Fully type annotated, ships `py.typed`, checked with `mypy --strict` in CI.

## License

MIT.
