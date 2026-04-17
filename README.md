# KernyxVideoMakerr

Pipeline local pour generer des videos verticales courtes avec `VideoJob` comme source de verite.

## Pipeline

Ordre par defaut :

1. `script_gen`
2. `voice_gen`
3. `scene_planner`
4. `visual_gen`
5. `edit_planner`
6. `assembler`

Le pipeline multi-scene garde des indices internes zero-based (`scene.index = 0..N-1`) et reserve le format one-based aux ids et noms de fichiers (`scene_01`, `scene_02`, ...).

## Installation

### Ubuntu

Dependances systeme :

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ffmpeg fontconfig fonts-dejavu-core
```

Installation runtime :

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Installation dev + tests :

```bash
python3 -m pip install -r requirements-dev.txt
```

### Windows

Prerequis :

- Python 3.11+
- `ffmpeg` et `ffprobe` dans le `PATH`
- une police TrueType disponible pour les sous-titres, idealement `DejaVu Sans` ou `Arial`

Installation :

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
py -3.11 -m pip install --upgrade pip
py -3.11 -m pip install -r requirements.txt
py -3.11 -m pip install -r requirements-dev.txt
```

## Preflight runtime

Le scheduler lance un preflight automatique avant la queue, sauf si `--skip-preflight` est passe.

Le preflight verifie :

- `python3`
- `edge-tts`
- `ffmpeg`
- `ffprobe`
- le rendu runtime des polices de sous-titres via FFmpeg/libass

Commandes utiles :

```bash
python3 scheduler.py --preflight --config config/topics_multi_scene_example.json
python3 scheduler.py --config config/topics_multi_scene_example.json --concurrency 1 --verbose
python3 scheduler.py --config config/topics_multi_scene_example.json --skip-preflight
```

## Tests

Depuis la racine du repo, `pytest` fonctionne sans `PYTHONPATH` manuel.

```bash
pytest -q
pytest -q tests/test_job_paths.py
pytest -q tests/test_multiscene_artifact_integrity.py
pytest -q tests/test_runtime_preflight.py
```

## Assets automation

Pour les jobs resolves en profil `automation`, `visual_gen` priorise les assets reels locaux avant le fallback synthetique si aucun asset explicite n'a deja ete fourni.

Ordre de priorite :

1. `scene.asset_path`
2. `scene_assets` explicites
3. catalogue explicite
4. catalogue implicite local (`tiktok/photo`, `tiktok/video`) pour l'automation
5. pool d'assets explicite
6. generation synthetique

Le premier visuel reste aussi expose via `source_media` pour la compatibilite legacy.

## Artefacts principaux

- `01_script.txt`
- `02_voice.mp3`
- `03_subtitles.srt`
- `04_scene_manifest.json`
- `05_visual_manifest.json`
- `06_edit_manifest.json`
- `scene_assets/scene_XX.*`
- `scene_clips/scene_XX.mp4`
- `06_scenes_concat.mp4`
- `07_audio_mix.m4a`
- `final.mp4`

## Exemple de run multi-scene

Config d'exemple :

- [config/topics_multi_scene_example.json](/home/vboxuser/projects/KernyxVideoMakerr/config/topics_multi_scene_example.json)
- [config/examples/edit_profiles.example.json](/home/vboxuser/projects/KernyxVideoMakerr/config/examples/edit_profiles.example.json)

Run :

```bash
python3 scheduler.py --config config/topics_multi_scene_example.json --concurrency 1 --verbose
```

## Audio SFX

Le montage multi-scene peut mixer automatiquement des SFX locaux si des assets audio sont disponibles.

Conventions supportees en V1 :

- racine auto-detectee `tiktok/asset_son`
- racines configurees via `metadata.edit_audio_roots`
- mapping explicite via `metadata.edit_audio_profiles.sfx`
- spread temporel via `metadata.edit_sfx_timing_spread_ms`
- fenetre anti-repetition via `metadata.edit_sfx_recent_window`

Metadonnees de trace principales :

- `sfx_enabled`
- `sfx_strategy`
- `sfx_timing_mode`
- `sfx_gain_strategy`
- `sfx_asset_count`
- `sfx_recent_files`
- `sfx_selection_log`
- `sfx_events`
- `sfx_mix_path`

## Notes de prod

- `VideoJob` reste la source de verite pour les artefacts et metadonnees.
- Les polices runtime resolues sont persistantes dans `job.metadata` via `subtitle_font_requested` / `subtitle_font_resolved`.
- Si une police demandee n'est pas disponible, le runtime tente un fallback `DejaVu Sans`, puis `Arial`, puis `sans-serif`.
