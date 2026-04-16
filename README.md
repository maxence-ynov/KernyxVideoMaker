# KernyxVideoMaker

Pipeline local pour generer des videos verticales courtes avec `VideoJob` comme source de verite.

## Multi-scenes V1

La V1 ajoute un pipeline synchronise scene par scene pour des videos TikTok plus dynamiques.

Pipeline par defaut :

1. `script_gen`
2. `voice_gen`
3. `scene_planner`
4. `visual_gen`
5. `edit_planner`
6. `assembler`

## Architecture

`VideoJob` reste l'objet central. Les etapes enrichissent progressivement `job.metadata` et les artefacts sur disque.

Champs cles de la V1 :

- `script_sections` : hook, points, cta produits par `script_gen`
- `edge_tts_voice` : voix TTS configurable via `VideoJob.metadata` (ex: `fr-FR-DeniseNeural`)
- `voice_duration_seconds` : duree audio reelle issue de `voice_gen`
- `scene_manifest` : timeline complete des scenes stockee dans `VideoJob.metadata`
- `scene_manifest_path` : copie JSON du manifeste dans le dossier artefacts du job
- `visual_scene_assets` : resume des assets rendus pour chaque scene
- `edit_manifest` : decisions de montage par scene
- `edit_manifest_path` : copie JSON du plan de montage
- `video_structure` : `single_scene` ou `multi_scene_v1`

## Scene Planner

`pipeline/scene_planner.py` segmente le script, assigne un type de scene et distribue la duree audio :

- `hook` -> scene d'accroche
- `body` -> scene `workflow`, `proof`, `interface` ou `explain` selon le texte
- `cta` -> scene de fermeture

Chaque scene contient :

- texte source
- mots-cles
- `visual_asset_mode`
- motion (`zoom_in`, `pan_left`, `pan_right`, `pan_up`)
- transition (`fade`)
- debut, fin et duree synchronises sur la voix

## Visuals

`pipeline/visual_gen.py` lit le manifeste et :

- genere un visuel dedie par scene dans `scene_assets/`
- ou reutilise une source media fournie si le job en contient deja une
- conserve `source_media` pour la compatibilite en le pointant vers le premier visuel

La logique reutilise le `visual_asset_mode` existant :

- si `visual_asset_mode` est explicite, il est respecte
- sinon il est choisi scene par scene a partir du type de scene

## Edit Planner

`pipeline/edit_planner.py` ajoute une couche de montage template-driven sans reproduire un NLE complet.

Pour chaque scene, le planner decide :

- `camera_pattern` + `camera_intensity`
- `transition_in` + `transition_out`
- `text_style`
- `overlay_style`
- `sfx_profile`
- `bgm_profile`
- `pace_role`

Regles par defaut :

- `hook` : rythme plus nerveux, `hook_focus`, pattern plus agressif
- `proof` : plus stable, `proof_clean`, `proof_frame` possible
- `explain` : lisible, `keyword_emphasis` ou `standard_subtitles`
- `cta` : plus calme, `cta_band` possible, peu de chaos visuel

## FFmpeg

`pipeline/assembler.py` monte la video finale en 2 temps :

1. rendu d'un clip par scene avec motion/camera pattern, overlays, transitions et captions par scene dans `scene_clips/`
2. concat des clips puis mux final avec voix et sound design optionnel

Compatibilite :

- si `edit_manifest` est present, l'assembleur utilise la nouvelle couche de montage
- si `edit_manifest` est absent, il retombe sur le comportement legacy
- si aucun asset audio de `bgm` ou `sfx` n'est trouve, le rendu continue sans casser le pipeline

Artefacts multi-scenes :

- `scene_manifest.json`
- `scene_assets/scene_XX.ppm`
- `06_edit_manifest.json`
- `scene_clips/scene_XX.mp4`
- `scenes.concat.mp4`
- `final.mp4`

Exemple de config de profils : `config/examples/edit_profiles.example.json`

## Exemple Concret

Fichier d'exemple : [`config/topics_multi_scene_example.json`](/home/vboxuser/projects/KernyxVideoMaker/config/topics_multi_scene_example.json)

Lancer :

```bash
python3 scheduler.py --config config/topics_multi_scene_example.json --concurrency 1 --verbose
```

Exemple de flux pour un sujet hooks TikTok :

1. `script_gen` produit `hook + 3 points + cta`
2. `voice_gen` cree `voice.mp3` via `edge-tts` et mesure la duree audio reelle
3. `scene_planner` cree 5 scenes synchronisees
4. `visual_gen` rend 5 visuels coherents
5. `assembler` cree 5 clips, les concatene, puis ajoute voix + SRT

## Smoke Test Realise

Validation e2e locale realisee avec succes sur un job de smoke test :

- `scene_count = 5`
- `video_structure = multi_scene_v1`
- `final.mp4` genere sans erreur
- duree finale observee : `29.4s`
