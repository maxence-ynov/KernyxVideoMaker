# Audit E2E sur 20 videos

## But

Mesurer la perception finale de 20 videos generees a la suite dans la niche automatisation IA.
Le jugement porte sur les sorties reelles, pas seulement sur les modules:

- script
- voix
- sous-titres
- visuel source
- rendu final `final.mp4`

## Jeu de test

Le set de 20 sujets se trouve dans [config/e2e_audit_20_topics.json](/home/vboxuser/projects/KernyxVideoMaker/config/e2e_audit_20_topics.json:1).

Principes du set:

- 1 seule niche: `ai_automation`
- 1 seul profil editorial vise: `automation`
- 20 sujets sur des sous-angles differents: erreurs, handoff, audit, CRM, review queue, edge cases, demos, fiabilite
- sujets volontairement assez proches pour tester la diversite sans perdre la coherence de niche

## Protocole

### 1. Generation

Lancer le pipeline en sequence, pas en parallele, pour tester la memoire anti-repetition telle qu'elle serait ressentie sur un vrai lot:

```bash
python3 scheduler.py \
  --config config/e2e_audit_20_topics.json \
  --jobs-dir tmp/e2e_audit_20/jobs \
  --artifacts-dir tmp/e2e_audit_20/artifacts \
  --concurrency 1
```

Pourquoi `--concurrency 1`:

- ordre de feed stable
- memoire anti-repetition observee dans de vraies conditions
- plus simple pour identifier les repetitions de run en run

### 2. Preparation de la revue

Pour chaque job, rassembler:

- `script.txt`
- `voice.mp3`
- `subtitles.srt`
- `source_media.*`
- `final.mp4`

Utiliser une scorecard unique: [docs/e2e_audit_scorecard_template.csv](/home/vboxuser/projects/KernyxVideoMaker/docs/e2e_audit_scorecard_template.csv:1)

### 3. Revue en 4 passes

Pass A, video par video, son active:

- regarder chaque `final.mp4` une seule fois, sans pause
- noter la comprehension en 2 secondes
- noter la credibilite, la voix, les sous-titres, le visuel et l'envie de publier

Pass B, video par video, son coupe:

- verifier si la promesse passe encore sans voix
- juger lisibilite des sous-titres et clarte du visuel

Pass C, feed-level:

- regarder les 20 videos a la suite dans l'ordre de generation
- laisser 2 a 3 secondes entre chaque video
- noter quand on a l'impression de voir "la meme video encore"

Pass D, revue externe:

- idealement 2 evaluateurs minimum
- un evaluateur technique
- un evaluateur edito ou growth qui ne connait pas le code

### 4. Seuils de decision

Une video est "publishable" si:

- aucune faute bloquante
- score global >= 3.8/5
- score "credibilite" >= 4/5
- score "humain" >= 3.5/5

Le lot de 20 est "feed-ready" si:

- au moins 15/20 videos sont publishable
- au moins 14/20 ont une ouverture percue comme distincte
- au plus 3/20 paraissent generiques ou templatises
- au plus 2/20 semblent hors niche ou edito faibles

## Criteres video par video

Noter chaque critere sur 1 a 5.

| Critere | Ce qu'on mesure | 1-2 | 3 | 4-5 |
|---|---|---|---|---|
| Hook percu | La promesse est-elle claire dans les 2 premieres secondes | vague, abstrait, lent | comprenable mais peu tendu | clair, net, envie de rester |
| Specificite niche | La video parle-t-elle vraiment automatisation IA | pourrait s'appliquer a n'importe quoi | quelques marqueurs niche | ancree dans des situations reelles d'automatisation |
| Credibilite du script | Presence de details concrets, erreurs reelles, outils, cas plausibles | conseils generiques | semi-concret | details plausibles et memorables |
| Humanite du script | Le texte sonne-t-il ecrit par quelqu'un qui a deja vu le probleme | trop propre, trop abstrait | melange | ton vivant, angle net, leger relief humain |
| Naturel de la voix | Prosodie, rythme, emphasis, absence de TTS obvious | monotone, mecanique | acceptable | naturel, soutenu, credibilite vocale |
| Qualite des sous-titres | Decoupage, timing, lisibilite, respiration | surcharge, mauvais breaks | lisible mais rigide | lisible, nerveux, bien synchronise |
| Credibilite visuelle | Le visuel ressemble-t-il a un vrai environnement de travail ou a une vraie preuve | faux dashboard, pseudo UI decorative | correcte | plausible, lisible, coherent avec le propos |
| Cohesion globale | Script, voix, sous-titres et visuel racontent-ils la meme promesse | couches decouplees | coherence partielle | meme angle partout |
| Publishabilite TikTok | Donneriez-vous le feu vert a la publication sans retouche lourde | non | oui avec retouches | oui presque tel quel |

### Fautes bloquantes video-level

Une seule faute bloquante suffit a marquer la video "non publishable":

- phrase d'ouverture trop generique ou incomprehensible
- detail faux ou peu plausible sur un outil ou un workflow
- voix clairement robotique ou penible a ecouter
- sous-titres difficilement lisibles sur mobile
- visuel qui fait "fake AI dashboard" sans rapport avec le sujet
- decalage fort entre voix, texte a l'ecran et promesse du script

## Criteres feed-level

| Critere | Question | Signal d'alerte |
|---|---|---|
| Diversite des hooks | Les 20 ouvertures donnent-elles 20 raisons de regarder | meme mecanique d'ouverture 4 fois ou plus |
| Diversite des preuves | On voit des resultats, erreurs, outils et exemples varies | meme mode de preuve qui revient en boucle |
| Diversite des CTA | Les sorties appellent-elles a l'action sans effet template | meme formule percue trop souvent |
| Diversite visuelle | Les 20 visuels changent vraiment de mode, theme et mise en page | feed qui ressemble a un seul template recolore |
| Cohesion de niche | Toutes les videos restent-elles dans la niche automatisation IA | videos generiques "productivite" ou "marketing" |
| Credibilite de marque | Le feed donne-t-il l'image d'un operateur expert | trop conceptuel, pas assez terrain |
| Densite de repetition | Combien de videos paraissent interchangeables | plus de 5 videos sur 20 se melangent en memoire |
| Fatigue de feed | A quel moment l'evaluateur a envie de swiper par lassitude | lassitude franche avant la video 10 |

### Questions feed-level a renseigner

- Quelles videos se ressemblent trop entre elles
- Quels motifs reviennent trop souvent dans les 5 premieres secondes
- Quels sujets semblent "hors canal"
- Quelle part du lot semble vraiment publishable telle quelle
- Si ce lot etait votre feed TikTok reel, le publieriez-vous sans honte

## Signaux qui trahissent encore un contenu trop IA

### Script

- promesses valables mais trop propres, sans angle conflictuel
- conseils generiques qui pourraient s'appliquer a n'importe quelle niche
- absence de details operatoires: exception path, handoff, audit, mapping, queue, review, fail state
- exces de symetrie dans les phrases
- CTA toujours bien ranges, trop previsibles

### Voix

- cadence identique d'une video a l'autre
- pauses gouvernees par la ponctuation et non par le sens
- aucune vraie emphasis sur le mot important
- impression de lecture au lieu d'impression de conviction

### Sous-titres

- blocs toujours de meme longueur
- coupee syntaxique correcte mais coupee editoriale faible
- ligne qui se termine au mauvais endroit sur mobile
- aucune variation de respiration entre une phrase forte et une phrase secondaire

### Visuel

- interfaces trop propres et trop abstraites
- dashboards sans variables reelles, sans signaux d'erreur plausibles, sans friction humaine
- memes compositions visuelles recyclees avec seulement une autre couleur
- labels comme `AI OPS SYSTEM` qui sonnent plus demo que vrai usage

### Rendu global

- le visuel illustre "de l'IA" au lieu d'illustrer la promesse du sujet
- voix, sous-titres et image ne mettent pas en avant le meme moment fort
- impression que le pipeline a bien tourne, mais qu'aucune video n'a ete vraiment decidee

## Correctifs prioritaires selon les resultats observes

### Cas 1. Les videos sont differentes mais peu credibles

Priorite:

1. enrichir la banque de preuves par niche
2. injecter des details d'exception et de fail states reels
3. remplacer les phrases abstraites par des scenes operatoires concretes

Concret:

- ajouter une librairie de cas reels par sous-angle automation
- forcer un niveau minimal de specificite par script
- refuser les scripts sans outil, erreur, signal ou consequence plausible

### Cas 2. Les videos sont credibles mais encore trop IA

Priorite:

1. casser la regularite du rythme de voix
2. casser la symetrie des phrases
3. rendre les sous-titres moins uniformes

Concret:

- varier davantage `voice_delivery_style`
- reecrire les hooks pour enlever la structure trop parfaite
- ajouter des regles de decoupage sous-titres plus editoriales que syntaxiques

### Cas 3. Les videos sont humaines mais trop repetitives

Priorite:

1. elargir les families de hook dans le profil `automation`
2. elargir les modes de preuve reellement distincts
3. diversifier plus fort les visuels sur les rolling windows

Concret:

- ajouter 2 a 3 nouvelles hook families `automation`
- ajouter des families de CTA moins template
- introduire plus de scenes visuelles propres a `review queue`, `audit trail`, `mapping failure`, `approval gate`

### Cas 4. Les videos sont credibles mais pas publishable TikTok

Priorite:

1. resserrer les 2 premieres secondes
2. renforcer la lisibilite mobile
3. mieux aligner promesse, sous-titres et image dans l'ouverture

Concret:

- reduire les hooks trop longs
- augmenter l'impact du premier ecran
- verifier que le premier bloc sous-titre porte la vraie promesse, pas l'introduction

### Cas 5. Le lot sort de la niche automatisation IA

Priorite:

1. renforcer le verrou editorial
2. enrichir le vocabulaire niche
3. supprimer les formulations generic business

Concret:

- durcir les mappings de niche
- maintenir une liste de concepts obligatoires par sous-angle
- blacklister certains patterns trop generiques

## Sortie attendue de l'audit

En fin d'audit, produire:

- un score video par video
- une liste des videos publishable sans retouche
- une liste des videos a retoucher legerement
- une liste des videos a rejeter
- les 5 motifs de repetition dominants
- les 5 signaux IA les plus visibles
- les 3 correctifs priorite 1 pour le prochain run

Le bon niveau d'exigence n'est pas "le pipeline marche".
Le bon niveau d'exigence est: "si ces 20 videos etaient postees sur un vrai compte TikTok automation IA, est-ce qu'elles protegeraient la perception d'expertise du compte".
