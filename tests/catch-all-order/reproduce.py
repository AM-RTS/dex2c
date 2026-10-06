"""Catch-all regression on a real DEX; optional baseline/current host JNI proof."""
import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent.parent
BUILD = HERE / 'build'
BASELINE = '2af11db9af7d813d5bc86c883459c1c3efe0896b'
# Assembled from the Smali method documented in README.md; no assembler needed.
DEX = base64.b64decode(
    'ZGV4CjAzNQCBXiKsSnHRVhZo15V+V1z1cu99Buz8QJFuAgAAcAAAAHhWNBIAAAAAAAAAAPAAAAAKAAAAcAAAAAcAAACYAAAA'
    'AQAAALQAAAABAAAAwAAAAAEAAADIAAAAAQAAANAAAAB+AQAA8AAAAPYBAAD5AQAAzQEAAFMCAAAUAgAA2wEAADoCAAD9AQAA'
    'TgIAAO8BAAAAAAAAAgAAAAMAAAAEAAAABQAAAAYAAAAHAAAAAQAAAAAAAACQAQAABQACAAgAAAABAAAACQAAAAEAAAABAAAA'
    'BAAAAAAAAAD/////AAAAAMUBAAAAAAAADQAAAAAAAAABAAAAAAAAAAEAAAAKAAAAcAAAAAIAAAAHAAAAmAAAAAMAAAABAAAA'
    'tAAAAAQAAAABAAAAwAAAAAUAAAABAAAAyAAAAAYAAAABAAAA0AAAAAAQAAABAAAA8AAAAAEQAAABAAAAkAEAAAEgAAABAAAA'
    'mAEAAAAgAAABAAAAxQEAAAIgAAAKAAAAzQEAAAMgAAABAAAAagIAAAEAAAAGAAAAAgABAAAAAQBqAgAABwAAACcBDQASEA8A'
    'DQASIA8AAAAAAAAAAQABAAF/AwEEAAABAAAJmAMMTENhdGNoT3JkZXI7ABJMamF2YS9sYW5nL09iamVjdDsABXByb2JlAAFJ'
    'AAJJTAAVTGphdmEvbGFuZy9UaHJvd2FibGU7ACRMamF2YS9sYW5nL0lsbGVnYWxBcmd1bWVudEV4Y2VwdGlvbjsAEkxqYXZh'
    'L2xhbmcvU3lzdGVtOwADb3V0ABVMamF2YS9pby9QcmludFN0cmVhbTsAAAEAAA=='
)

JAVA = '''import java.net.*;
import java.nio.file.*;
import java.lang.reflect.*;
public class Compare {
    public static void main(String[] args) throws Exception {
        System.load(Paths.get(args[1]).toAbsolutePath().toString());
        try (URLClassLoader loader = new URLClassLoader(
                new URL[]{Paths.get(args[0]).toUri().toURL()}, null)) {
            Method original = loader.loadClass("CatchOrder").getMethod("probe", Throwable.class);
            Throwable[] inputs = {new IllegalArgumentException(), new NumberFormatException(),
                new IllegalStateException(), new Error(), null};
            for (Throwable input : inputs) {
                int expected = (Integer) original.invoke(null, new Object[]{input});
                int actual = CatchOrder.probe(input);
                System.out.println((input == null ? "null" : input.getClass().getSimpleName())
                    + "\\t" + expected + "\\t" + actual);
            }
        }
    }
}
class CatchOrder { public static native int probe(Throwable input); }
'''


def run(*args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', help='also run baseline/current real JNI comparison')
    parser.add_argument('--dex2jar-lib', type=Path, help='dex2jar distribution lib directory (required with --native)')
    args = parser.parse_args()
    if args.native and not args.dex2jar_lib:
        parser.error('--native requires --dex2jar-lib')
    BUILD.mkdir(exist_ok=True)
    dex = BUILD / 'original.dex'
    dex.write_bytes(DEX)
    assert hashlib.sha256(DEX).hexdigest() == '280737a6063eeaea2d5fc85a2435127b61c9dc9ee0282cabf9153e7887de0f5a'

    sys.path.insert(0, str(REFERENCE))
    from dex2c.compiler import auto_vm, Dex2C
    from androguard.core.analysis.analysis import Analysis
    from androguard.core.bytecodes import dvm

    vm = auto_vm(str(dex))
    method = next(m for c in vm.get_classes() for m in c.get_methods())
    handler = method.get_code().get_handlers().get_list()[0]
    encoded_types = [vm.get_cm_type(h.get_type_idx()) for h in handler.get_handlers()]
    assert encoded_types == ['Ljava/lang/IllegalArgumentException;']
    assert handler.get_size() == -1  # One typed handler, followed by catch-all.
    assert len(method.get_code().get_tries()) == 1
    parsed = dvm.determineException(vm, method)
    assert [h[0] for h in parsed[0][2:]] == [
        'Ljava/lang/IllegalArgumentException;', 'Ljava/lang/Throwable;']

    source, _ = Dex2C(vm, Analysis(vm), False, False).get_source_method(method)
    (BUILD / 'fixed.cpp').write_text('#include "Dex2C.h"\n' + source)
    landing = source.split('EX_LandingPad_0:\n', 1)[1]
    assert landing.index('java/lang/IllegalArgumentException') < landing.index('java/lang/Throwable')
    print('PASS: real DEX parser and generated landing pad preserve typed catch before catch-all.')
    if not args.native:
        return
    libs = str(args.dex2jar_lib.resolve() / '*')
    jar = BUILD / 'original.jar'
    run('java', '-cp', libs, 'com.googlecode.dex2jar.tools.Dex2jarCmd', '-f', '-o', jar, dex)
    (BUILD / 'original-javap.txt').write_text(run('javap', '-c', '-classpath', jar, 'CatchOrder'))

    java_home = Path(shutil.which('javac')).resolve().parent.parent
    support = REFERENCE / 'project/jni/nc'
    android = BUILD / 'android'
    android.mkdir(exist_ok=True)
    # Host-only logging shim. Exception macros/helpers are compiled from the reference verbatim.
    (android / 'log.h').write_text('#pragma once\n')
    # Compile just the two required helpers verbatim; other Android runtime code is unused.
    runtime = (support / 'Dex2C.cpp').read_text()
    helpers = ('void d2c_throw_exception(' + runtime.split('void d2c_throw_exception(', 1)[1]
               .split('\nvoid d2c_filled_new_array', 1)[0]
               + 'bool d2c_is_instance_of(' + runtime.split('bool d2c_is_instance_of(', 1)[1]
               .split('\nbool d2c_check_cast', 1)[0])
    (BUILD / 'helpers.cpp').write_text('#include "Dex2C.h"\n#include "ScopedLocalRef.h"\n' + helpers)
    (BUILD / 'Compare.java').write_text(JAVA)
    run('javac', '-d', BUILD, BUILD / 'Compare.java')
    original_determine = dvm.determineException
    results = {}
    try:
        # Use the exact baseline parser from Git, keeping all other generation/runtime code identical.
        old_source = run('git', '-C', REFERENCE, 'show', BASELINE + ':androguard/core/bytecodes/dvm.py')
        node = next(n for n in ast.parse(old_source).body if isinstance(n, ast.FunctionDef) and n.name == 'determineException')
        scope = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<baseline determineException>', 'exec'), scope)
        for variant in ('baseline', 'fixed'):
            dvm.determineException = scope['determineException'] if variant == 'baseline' else original_determine
            vm = auto_vm(str(dex))
            method = next(m for c in vm.get_classes() for m in c.get_methods())
            source, _ = Dex2C(vm, Analysis(vm), False, False).get_source_method(method)
            cpp = BUILD / (variant + '.cpp')
            cpp.write_text('#include "Dex2C.h"\n' + source)
            library = BUILD / (variant + ('.dll' if os.name == 'nt' else '.so'))
            run('g++', '-std=c++14', '-shared', '-O2', *(['-static'] if os.name == 'nt' else ['-fPIC']),
                '-I', BUILD, '-I', support,
                '-I', java_home / 'include', '-I', java_home / 'include' / ('win32' if os.name == 'nt' else 'darwin' if sys.platform == 'darwin' else 'linux'),
                cpp, BUILD / 'helpers.cpp', '-o', library)
            rows = run('java', '-Xcheck:jni', '-cp', BUILD, 'Compare', jar, library).splitlines()
            results[variant] = [dict(zip(('input', 'dexViaJvm', 'native'), row.split('\t')))
                                for row in rows if '\t' in row]
            print(variant + ':\n' + '\n'.join(rows))
    finally:
        dvm.determineException = original_determine
    assert [int(r['dexViaJvm']) for r in results['baseline']] == [1, 1, 2, 2, 2]
    assert [int(r['native']) for r in results['baseline']] == [2, 2, 2, 2, 2]
    assert [int(r['dexViaJvm']) for r in results['fixed']] == [1, 1, 2, 2, 2]
    assert [int(r['native']) for r in results['fixed']] == [1, 1, 2, 2, 2]
    (BUILD / 'results.json').write_text(json.dumps({
        'baselineCommit': BASELINE,
        'testedCommit': run('git', '-C', REFERENCE, 'rev-parse', 'HEAD').strip(),
        'dexSha256': hashlib.sha256(DEX).hexdigest(),
        'encodedTypedHandlers': encoded_types, 'encodedCatchAll': handler.get_catch_all_addr(),
        'parsedHandlerOrder': [h[0] for h in parsed[0][2:]],
        'boundary': 'DEX executed via dex2jar/JVM; generated C++ and reference helpers via host JNI; ART untested',
        'results': results}, indent=2) + '\n')
    print('PASS: semantic mismatch reproduced; fixed parser restores equivalence.')


if __name__ == '__main__':
    main()
