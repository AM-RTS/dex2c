# Typed catch before catch-all

`determineException()` used to prepend catch-all as `java/lang/Throwable`
before typed handlers. The generated JNI landing pad therefore sent every
exception to catch-all, making the typed handler unreachable. The fix appends
catch-all after the ordered typed handlers, as required by the
[DEX format](https://source.android.com/docs/core/runtime/dex-format#encoded-catch-handler).

## Minimal valid input

The script embeds the assembled 622-byte DEX for this method, so reproducing
the test does not require a Smali assembler:

```smali
.class public LCatchOrder;
.super Ljava/lang/Object;
.method public static probe(Ljava/lang/Throwable;)I
    .registers 2
    :try_start
    throw p0
    :try_end
    .catch Ljava/lang/IllegalArgumentException; {:try_start .. :try_end} :typed
    .catchall {:try_start .. :try_end} :fallback
    :typed
    move-exception v0
    const/4 v0, 0x1
    return v0
    :fallback
    move-exception v0
    const/4 v0, 0x2
    return v0
.end method
```

The DEX has one protected range and one typed catch followed by catch-all
(`encoded_catch_handler.size == -1`). Its SHA-256 is
`280737a6063eeaea2d5fc85a2435127b61c9dc9ee0282cabf9153e7887de0f5a`.

## Run

From the repository root, with its existing Python dependencies installed:

```sh
python -m pip install -r requirements.txt
python tests/catch-all-order/reproduce.py
```

The default check parses the actual DEX and asserts both parsed handler order
and generated landing-pad order. It failed before the fix and passes after it.

For the executable comparison, also install a JDK (with `java`, `javac`, and
`javap` on PATH), `g++`, and a dex2jar distribution containing its `lib` JARs:

```sh
python tests/catch-all-order/reproduce.py --native --dex2jar-lib /path/to/dex2jar/lib
```

This mode needs the baseline commit `2af11db9af7d813d5bc86c883459c1c3efe0896b`
in local Git history. It loads that commit's exact parser function in memory,
then compares baseline and fixed generation from the same input. It never
checks out or modifies source files. Exception macros and the two required
C++ runtime helpers are compiled verbatim; an empty Android logging header
allows the host JNI build. Both JVM runs use `-Xcheck:jni`.

## Measured results

Confirmed on Windows with Python 3.11, JDK 11, MinGW g++, and the existing
dex2jar toolchain on 2026-10-07:

| Input | Input DEX via dex2jar/JVM | Baseline JNI | Fixed JNI |
|---|---:|---:|---:|
| IllegalArgumentException | 1 | **2** | 1 |
| NumberFormatException (subclass) | 1 | **2** | 1 |
| IllegalStateException | 2 | 2 | 2 |
| Error | 2 | 2 | 2 |
| null (throw produces NullPointerException) | 2 | 2 | 2 |

The test asserts all ten native results. Regenerated DEX, Java exception-table
disassembly, baseline/fixed C++, JNI libraries, and `results.json` stay in the
ignored `build/` directory.

This proves an executable semantic difference and its correction through
real host JNI. The input DEX is executed via dex2jar/JVM; direct ART execution
and Android ABI behavior remain untested.
