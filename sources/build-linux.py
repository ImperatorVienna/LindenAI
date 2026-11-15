#!/usr/bin/env python3
"""Build relocatable Git + HTTPS from pinned source, without host Git/libcurl dependencies."""
import hashlib, json, os, pathlib, platform, shutil, subprocess, tarfile, urllib.request
here = pathlib.Path(__file__).resolve().parent
root = here.parents[3]
cache = root / '.cache/linden-git-source'
work = root / '.cache/linden-git-build'
prefix = work / 'deps'
out = here.parents[1] / 'src-tauri/resources/git'
sources = json.loads((here / 'sources.json').read_text())
if platform.system() != 'Linux': raise SystemExit('Run the Linux Git build on Linux')
cache.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
env = {**os.environ, 'CFLAGS':'-O2 -fPIC', 'PKG_CONFIG_PATH':str(prefix/'lib/pkgconfig'), 'PKG_CONFIG_LIBDIR':str(prefix/'lib/pkgconfig')}
jobs = str(min(os.cpu_count() or 2, 8))
def run(args, cwd):
    print('+', ' '.join(map(str,args)), flush=True)
    subprocess.run(list(map(str,args)), cwd=cwd, env=env, check=True)
dirs = {}
archives = []
for name, spec in sources.items():
    archive = cache / spec['url'].rsplit('/',1)[1]
    if not archive.exists(): urllib.request.urlretrieve(spec['url'], archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != spec['sha256']: raise SystemExit('SHA256 mismatch: '+name)
    archives.append(archive)
    folder = work / (name+'-'+spec['version'])
    if not folder.exists():
        with tarfile.open(archive) as tar: tar.extractall(work, filter='data')
    dirs[name]=folder
run(['./configure','--static','--prefix='+str(prefix)], dirs['zlib'])
run(['make','-j'+jobs],dirs['zlib']);run(['make','install'],dirs['zlib'])
run(['./config','no-shared','no-tests','--prefix='+str(prefix),'--libdir=lib','--openssldir=/etc/ssl'],dirs['openssl'])
run(['make','-j'+jobs],dirs['openssl']);run(['make','install_sw'],dirs['openssl'])
run(['./configure','--prefix='+str(prefix),'--disable-shared','--enable-static','--with-openssl='+str(prefix),'--with-zlib='+str(prefix),'--without-libpsl','--without-libidn2','--without-brotli','--without-zstd','--without-libssh2','--without-nghttp2','--without-nghttp3','--without-ngtcp2','--disable-ldap','--disable-ldaps','--disable-manual','--without-gssapi','--with-ca-bundle=/etc/ssl/certs/ca-certificates.crt','--with-ca-path=/etc/ssl/certs'],dirs['curl'])
run(['make','-j'+jobs],dirs['curl']);run(['make','install'],dirs['curl'])
flags=['prefix='+str(out),'RUNTIME_PREFIX=YesPlease','NO_GETTEXT=YesPlease','NO_PERL=YesPlease','NO_TCLTK=YesPlease','NO_EXPAT=YesPlease','NO_OPENSSL=YesPlease','CURL_CONFIG='+str(prefix/'bin/curl-config'),'CURLDIR='+str(prefix),'CURL_LDFLAGS='+subprocess.check_output([prefix/'bin/curl-config','--static-libs'],text=True).strip(),'ZLIB_PATH='+str(prefix)]
run(['make','-j'+jobs,*flags],dirs['git'])
if out.exists(): shutil.rmtree(out)
run(['make',*flags,'install'],dirs['git'])
# Canonicalize identical ELF helpers, then strip debug symbols once per binary.
seen = {}
for file in sorted((out/'bin').iterdir()) + sorted((out/'libexec/git-core').iterdir()):
    if not file.is_file() or file.is_symlink(): continue
    data = file.read_bytes()
    if not data.startswith(b'\x7fELF'): continue
    digest = hashlib.sha256(data).hexdigest()
    if digest in seen:
        target = seen[digest]; file.unlink(); file.symlink_to(os.path.relpath(target,file.parent))
    else: seen[digest] = file
for file in seen.values(): run(['strip','--strip-unneeded',file],out)
# A wrapper scopes certificate fallback and helper lookup to this Git process.
launch=out/'launcher';launch.mkdir()
(launch/'git').write_text('''#!/bin/sh
root=${0%/launcher/git}
export PATH="$root/bin:$root/libexec/git-core:$PATH"
export GIT_EXEC_PATH="$root/libexec/git-core"
export GIT_TEMPLATE_DIR="$root/share/git-core/templates"
if [ -z "${GIT_SSL_CAINFO:-}" ] && [ ! -f /etc/ssl/certs/ca-certificates.crt ]; then
  export GIT_SSL_CAINFO="$root/ca-bundle.crt"
fi
exec "$root/bin/git" "$@"
''')
(launch/'git').chmod(0o755)
shutil.copyfile('/etc/ssl/certs/ca-certificates.crt',out/'ca-bundle.crt')
licenses=out/'licenses';licenses.mkdir()
for name,file in [('git','COPYING'),('curl','COPYING'),('openssl','LICENSE.txt'),('zlib','README')]:shutil.copyfile(dirs[name]/file,licenses/(name+'.txt'))
# Provide corresponding unmodified source and the exact build instructions with binaries.
source_out=out/'sources';source_out.mkdir()
for archive in archives:shutil.copyfile(archive,source_out/archive.name)
shutil.copyfile(__file__,source_out/'build-linux.py');shutil.copyfile(here/'sources.json',source_out/'sources.json')
(out/'manifest.json').write_text(json.dumps({'platform':platform.system().lower()+'-'+platform.machine(),'components':sources,'buildSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n')
for binary in [out/'bin/git',out/'libexec/git-core/git-remote-http']:
    libs=subprocess.check_output(['ldd',binary],text=True)
    print(libs)
    if any(name in libs for name in ['libcurl','libssl','libcrypto','libz.so','not found']):raise SystemExit('Unexpected external dependency: '+str(binary))
run([out/'launcher/git','--version'],out)
print('Built',out)
