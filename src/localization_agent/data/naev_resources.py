"""Run fixed upstream Naev text-resource generators in a separate working copy."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REVISION = '123fdc1be96d350fdff04cd2f61e2c66c99badd5'
ASSET_REVISION = '7dcc63f767d6657b737686722f868575fbf8e5c8'


def run(args: list, cwd: Path, env: dict | None = None) -> str:
    result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'Failed {args[0]}: {result.stderr[-4000:]}\n{result.stdout[-2000:]}')
    return result.stdout


def meson_dictionary(path: Path, variable: str) -> dict:
    """The pinned upstream generator specifications are literal dictionaries."""
    value = path.read_text().split(variable + ' = ', 1)[1]
    return ast.literal_eval(value[:value.rfind('}')+1])


def generate(raw: Path, assets: Path, work: Path, gettext_bin: Path) -> tuple[Path, dict]:
    raw, assets, work, gettext_bin = [p.resolve() for p in (raw, assets, work, gettext_bin)]
    for root, expected in ((raw, REVISION), (assets, ASSET_REVISION)):
        if run(['git', 'rev-parse', 'HEAD'], root).strip() != expected:
            raise ValueError(f'Wrong source revision: {root}; expected {expected}')
    if work.exists() and any(work.iterdir()):
        raise ValueError('Build work directory must be new or empty')
    work.mkdir(parents=True, exist_ok=True)
    source = work / 'source'
    source.mkdir()
    for name in ('dat', 'src', 'utils', 'po'):
        shutil.copytree(raw/name, source/name, symlinks=True)
    for name in ('LICENSE', 'gpl.txt', 'meson.build'):
        shutil.copyfile(raw/name, source/name)
    loading = source/'assets/gfx/loading'
    loading.mkdir(parents=True)
    for path in sorted((assets/'gfx/loading').glob('*.txt')):
        shutil.copyfile(path, loading/path.name)
    env = {**os.environ, 'PATH': str(gettext_bin)+os.pathsep+os.environ.get('PATH', ''),
           'GETTEXTDATADIRS': str(source/'po'), 'LC_ALL': 'C.UTF-8'}
    env['PYTHONPATH'] = os.pathsep.join(str(Path(p).resolve()) for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p)
    generated = []
    def record(output, generator, inputs):
        generated.append({'path': output.relative_to(source).as_posix(),
                          'generator': generator.relative_to(source).as_posix(),
                          'inputs': [p.relative_to(source).as_posix() for p in inputs],
                          'sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
    bioship = source/'dat/outfits/bioship'
    bio_out = source/'build/dat/outfits/bioship'
    bio_out.mkdir(parents=True)
    for spec in meson_dictionary(bioship/'meson.build', 'biooutfits').values():
        template = bioship/'templates'/(spec['template']+'.xml.template')
        outputs = [bio_out/name for name in spec['output']]
        run([sys.executable, bioship/'generate.py', template, '-o', *outputs], source, env)
        for output in outputs:
            record(output, bioship/'generate.py', [template]+[source/'dat/outfits'/p for p in spec.get('depend_files', [])])
    special = source/'dat/outfits/generated'
    special_out = source/'build/dat/outfits/generated'
    special_out.mkdir(parents=True)
    special_outputs = []
    for spec in meson_dictionary(special/'meson.build', 'customoutfits').values():
        inp = source/'dat/outfits'/spec['input']
        output = special_out/spec['output']
        run([sys.executable, special/spec['command'], inp, output], source, env)
        record(output, special/spec['command'], [inp])
        special_outputs.append(output)
    # Match the pinned Meson targets: raw outfits plus special generated outfits;
    # bioships are dependencies, not additional encyclopedia targets in this revision.
    for kind in ('outfits', 'ships'):
        generator = source/f'dat/naevpedia/{kind}/{kind}.py'
        outputs = source/f'build/dat/naevpedia/{kind}'
        outputs.mkdir(parents=True)
        inputs = sorted((source/f'dat/{kind}').rglob('*.xml'))
        if kind == 'outfits':
            inputs += special_outputs
        for inp in inputs:
            output = outputs/(inp.stem+'.md')
            if output.exists():
                raise ValueError(f'Conflicting generated encyclopedia filename: {output}')
            run([sys.executable, generator, inp, '-o', output], source, env)
            record(output, generator, [inp])
    python = sys.executable
    run([python, source/'po/credits_pot.py', 'po/credits.pot', 'dat/AUTHORS',
         *[p.relative_to(source) for p in sorted(loading.glob('*.txt'))]], source, env)
    run([python, source/'po/toml_pot.py', 'po/toml.pot', 'dat/damagetype', 'dat/slots', 'dat/start.toml'], source, env)
    markdown = sorted((source/'dat/naevpedia').rglob('*.md'))+sorted((source/'build/dat/naevpedia').rglob('*.md'))
    run([python, source/'po/naevpedia_pot.py', 'po/naevpedia.pot', *[p.relative_to(source) for p in markdown]], source, env)
    run([gettext_bin/'xgettext', '--its=po/its/translation.its', '--from-code=UTF-8',
         *[p.relative_to(source) for p in sorted((source/'build/dat/outfits').rglob('*.xml'))], '-o', 'po/outfits_generated.pot'], source, env)
    generated_pot = source/'po/outfits_generated.pot'
    generated_pot.write_text(generated_pot.read_text().replace('charset=CHARSET', 'charset=UTF-8'))
    # Same source families as upstream update-po.sh; exclude geometric polygons.
    potfiles = ['po/'+p+'.pot' for p in ('naevpedia', 'physfs', 'credits', 'toml', 'outfits_generated')]
    potfiles += sorted(p.relative_to(source).as_posix() for p in (source/'dat').rglob('*.xml')
                       if not any(part in ('space_polygon', 'ship_polygon') for part in p.parts))
    potfiles += sorted(p.relative_to(source).as_posix() for root in ('dat', 'src') for p in (source/root).rglob('*')
                       if p.suffix in ({'.lua'} if root=='dat' else {'.c', '.h', '.rs'}))
    potfiles += sorted(p.relative_to(source).as_posix() for p in (source/'dat/outfits').rglob('*.py'))
    (source/'po/POTFILES.in').write_text('\n'.join(potfiles)+'\n')
    run([gettext_bin/'xgettext', '--files-from=po/POTFILES.in', '--from-code=UTF-8', '--add-comments',
         '--keyword=_', '--keyword=N_', '--keyword=n_:1,2', '--keyword=p_:1c,2',
         '--keyword=gettext.gettext_noop', '--package-name=naev', '-o', 'po/naev-rebuilt.pot'], source, env)
    metadata = {'revision': REVISION, 'asset_revision': ASSET_REVISION,
                'gettext_version': run([gettext_bin/'xgettext', '--version'], source).splitlines()[0],
                'python': sys.version.split()[0], 'generated': generated, 'potfiles': potfiles}
    (work/'generation.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')
    return source, metadata
