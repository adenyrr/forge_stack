# forge_stack

Deux projets Compose indépendants, à déployer sur **deux VM** :

- `gitlab-ce/compose.yaml` : GitLab CE, Registry et SSH.
- `runner/compose.yaml` : GitLab Runner avec exécuteur Docker rootless local.

Les limites CPU/RAM, la rotation des logs et les options Omnibus (SMTP, OIDC
Authentik, signature Gitaly, sauvegardes, Registry, optimisations mémoire/disque)
sont conservées. Les images sont épinglées par version et digest SHA-256.

### Versions vérifiées le 3 novembre 2026

| Image | Dernière version stable retenue | Digest SHA-256 |
| --- | --- | --- |
| `gitlab/gitlab-ce` | `19.5.3-ce.0` | `6863d156a1a477bd6f69fc674b671d0163cd8b06bf63bb17cc4a4a803b78b27c` |
| `gitlab/gitlab-runner` | `v19.5.2` | `6d4cdfe23443fa5463bffa65cba9074a4517f68784756b69bcc362ca86f3d4c6` |
| `alpine` (image de job par défaut dans les exemples) | `3.24.3` | `be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a` |

Sources : tags officiels Docker Hub de [GitLab CE](https://hub.docker.com/r/gitlab/gitlab-ce/tags),
du [Runner](https://hub.docker.com/r/gitlab/gitlab-runner/tags) et
d'[Alpine](https://hub.docker.com/_/alpine/tags). Les digests des manifestes ont
été contrôlés directement via `registry-1.docker.io` et correspondent aussi
aux tags `latest` au moment du contrôle. Les références retenues restent
immuables : aucun `latest`, `nightly`, `rc` ou `bleeding` n'est utilisé.
Les digests sont ceux des index multi-architectures, pas d'une seule plateforme.

**Instance existante : ne pas démarrer directement cette nouvelle image sur
les anciens volumes sans plan de mise à niveau.** La cible passe de GitLab
19.2.4 à 19.5.3. Vérifier la version réellement installée et le
[chemin de mise à niveau officiel](https://docs.gitlab.com/update/upgrade_paths/),
appliquer le dernier correctif du palier 19.2 (19.2.10 à la date du contrôle)
avant de rejoindre 19.5.3, et attendre la fin des migrations en arrière-plan
à chaque étape. Sauvegarder et lire les notes de version avant toute opération.
La modification de ces fichiers ne réalise aucun déploiement ni migration.

## Prérequis rootless — sur chaque VM

- Docker rootless et Compose v2 installés pour `deploy`, **UID 1001**.
- Exécuter les commandes comme `deploy`, **sans `sudo docker`**, sur son démon
  (`DOCKER_HOST=unix:///run/user/1001/docker.sock` ou contexte `rootless`).
- Socket existant, subuid/subgid configurés, service utilisateur Docker activé
  et lingering activé par un administrateur (`loginctl enable-linger deploy`).
- cgroup v2 avec délégation systemd pour appliquer les limites CPU/RAM.
- Stockage local compatible avec Docker rootless, espace et RAM suffisants
  pour GitLab (limite conservée : 6 Gio ; prévoir aussi l'OS et Docker).

Vérifier `id -u`, `docker context show`, `docker info` (rootless, cgroups)
et `test -S /run/user/1001/docker.sock`. Les deux Compose s'exécutent avec
`user: "0:0"` **dans le namespace rootless**, ce qui correspond à UID 1001
sur l'hôte. Forcer `user: "1001:1001"` casserait notamment Omnibus.
Ne pas appliquer de `chown -R 1001` aux données internes GitLab : les UID
des services sont mappés via les subuid du démon.

## GitLab et Traefik

```sh
cd gitlab-ce
cp .env.example .env
chmod 600 .env
```

Renseigner les domaines, le fuseau horaire, SMTP et OIDC, et `TRUSTED_PROXIES`
(liste d'IP/CIDR séparés par des virgules, sans apostrophe). Cette liste doit
contenir uniquement les proxies réellement de confiance, y compris le réseau
Docker de Traefik en mode local et les proxies amont nécessaires ; les plages
privées complètes ne sont plus implicitement approuvées. Les secrets peuvent
être injectés par Komodo plutôt qu'écrits dans `.env`. Les valeurs sont
interpolées dans du Ruby comme dans le Compose initial : échapper les caractères
Ruby particuliers (apostrophes, antislashs) dans les secrets injectés.

### Traefik sur la VM GitLab (mode par défaut)

Déployer [internal_traefik](https://github.com/adenyrr/internal_traefik) sur le
**même démon rootless** que GitLab. Il crée le réseau externe `traefik` et utilise
`/run/user/1001/docker.sock` via son socket-proxy en lecture seule. GitLab ne
monte aucun socket et ne rejoint pas le réseau privé du socket-proxy.

Les labels utilisent les conventions du dépôt : `websecure`, `letsencrypt`,
ports internes HTTP 80 et 5050. La redirection HTTP → HTTPS est assurée par
l'entrypoint `web` d'internal_traefik, sans dépendre d'un middleware inexistant
`redirect-https@file`. Laisser `HTTP_BIND_IP=127.0.0.1` ; Traefik utilise le réseau
Docker, pas les ports publiés en boucle locale.

**Ne pas ajouter de ForwardAuth Authentik devant GitLab ou le Registry** :
GitLab conserve son OIDC natif et les clients Git/Registry leur authentification
propre. Configurer les DNS et le chemin BunkerWeb → Traefik pour les deux domaines.

### Traefik sur une autre VM (alternative)

Les réseaux bridge et la découverte Docker via socket-proxy **ne traversent
pas les hôtes**. Ne pas exposer le socket-proxy sur le LAN pour y remédier.

1. Sur GitLab, définir `HTTP_BIND_IP` à l'IP privée joignable de cette VM.
2. Créer un réseau bridge local `traefik` (`docker network create traefik`) pour
   satisfaire le Compose ; ce réseau n'établit aucune liaison inter-VM.
3. Copier `gitlab-ce/traefik-dynamic.example.yaml` dans le dossier `dynamic/`
   d'internal_traefik, remplacer les domaines et l'IP d'exemple, puis vérifier
   que le provider fichier l'a chargé. Les labels de GitLab ne sont pas vus
   par ce Traefik distant ; son socket-proxy reste strictement local.
4. Autoriser les ports privés 8081 et 5050 **uniquement depuis la VM Traefik**.
   Configurer `TRUSTED_PROXIES` selon les adresses réellement observées ; le NAT
   rootless peut modifier l'adresse source. Vérifier les en-têtes et les logs.

Ce mode transporte HTTP entre Traefik et GitLab, comme le backend initial :
le réserver à un réseau privé filtré/de confiance ou à un tunnel chiffré. Pour
un réseau non fiable, prévoir TLS sur le backend avec validation des certificats.
Ne publier ni 8081 ni 5050 sur Internet.

### Démarrage

Depuis `gitlab-ce/`, après préparation de Traefik et des variables :

```sh
docker compose config --quiet
docker compose up -d
docker compose ps
```

Le démarrage Omnibus peut être long (healthcheck avec délai initial de 600 s).
Vérifier les deux domaines en HTTPS, l'OIDC, SMTP, un clone Git et un push/pull
Registry. La clé de signature Gitaly existante doit rester disponible dans
`/etc/gitlab/gitaly/signing_key` ; ne pas la générer ou la remplacer à l'aveugle.
SSH reste sur **2222** par défaut, sans passage par le routeur HTTP Traefik :
filtrer ce port et, si nécessaire, configurer un transfert TCP dédié. Si le port
public change, adapter `GITLAB_SSH_PORT` et le transfert correspondant.

## Runner — sur sa propre VM

Il n'a ni dépendance Compose vers GitLab, ni réseau partagé avec lui, ni port
entrant, ni route Traefik. Il contacte GitLab en HTTPS via son FQDN, comme les
conteneurs de jobs : DNS, connectivité et chaîne de confiance TLS doivent être
valides depuis les deux. Autoriser les sorties nécessaires vers GitLab, le
Registry, DNS et les dépôts d'images/paquets utilisés par les jobs.

```sh
cd runner
cp .env.example .env
chmod 600 .env
install -d -m 700 config
docker compose config --quiet
```

Créer le Runner dans l'interface GitLab et récupérer son **jeton
d'authentification** (`glrt-…`, pas l'ancien jeton d'enregistrement). Depuis
un shell Bash, saisir le jeton sans l'inscrire dans l'historique :

```bash
read -rsp 'Jeton du runner : ' RUNNER_TOKEN
export RUNNER_TOKEN
export CI_SERVER_URL=https://gitlab.example.net
docker compose run --rm -e RUNNER_TOKEN -e CI_SERVER_URL gitlab-runner register \
  --non-interactive --name runner-vm --executor docker \
  --docker-host unix:///var/run/docker.sock \
  --docker-image alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a --docker-volumes /cache
unset RUNNER_TOKEN CI_SERVER_URL
chmod 600 config/config.toml
```

Remplacer le domaine avant exécution. Adapter le fichier généré en suivant
`config.toml.example` : `concurrent = 1`, limites des jobs, `privileged = false`
et `services_privileged = false`, tout en conservant le vrai jeton. Les limites
Compose du gestionnaire Runner **ne limitent pas** les jobs lancés comme
conteneurs frères : les limites de `[runners.docker]` sont donc importantes.
Les services auxiliaires des jobs consomment aussi les ressources de la VM.

```sh
docker compose up -d
docker compose exec gitlab-runner gitlab-runner verify
docker compose ps
```

Valider ensuite un pipeline avec clone, artifacts et accès au Registry. Si une
CA privée est utilisée, installer sa chaîne dans le Runner **et** dans les
images/helper/jobs concernés ; ne pas désactiver la validation TLS.

Le Runner doit pouvoir créer/supprimer des conteneurs : le socket-proxy
**en lecture seule de Traefik ne convient pas** à l'exécuteur Docker. Il utilise
directement le socket **rootless de sa VM dédiée**, jamais le socket rootful
`/var/run/docker.sock` de l'hôte. Ce socket donne le contrôle du démon de
`deploy` : n'exécuter que des projets de confiance, utiliser les restrictions
de projets/tags et les runners protégés selon les besoins. Ne pas monter le
socket dans les jobs ; ne pas activer de DinD privilégié par défaut.
La configuration runtime, qui contient le jeton, est exclue de Git.

## Migration, sauvegardes et retour arrière

- Aucun volume ni conteneur existant n'est modifié par ces fichiers seuls.
  Planifier une interruption et sauvegarder avant toute bascule.
- Séparer la migration de VM de la mise à niveau : restaurer d'abord à la
  version/édition d'origine (adapter temporairement la référence d'image),
  valider la restauration, puis suivre les paliers vers la cible ci-dessus.
  Un retour d'image vers une ancienne version après migration de la base
  n'est pas un rollback sûr : restaurer une sauvegarde compatible.
- GitLab conserve le nom de projet **`forge`** et ses volumes
  `forge_gitlab-config`, `forge_gitlab-logs`, `forge_gitlab-data` si le nom
  d'origine n'avait pas été surchargé avec `-p`/`COMPOSE_PROJECT_NAME`. Relever les
  noms réellement utilisés avant migration ; ne pas démarrer sur des volumes
  vides par erreur. Le changement de dossier seul ne migre aucune donnée.
- Si GitLab reste sur le même démon, arrêter l'ancien projet sans supprimer
  ses volumes, puis démarrer le nouveau. Sur une autre VM, transférer/restaurer
  avec la procédure officielle GitLab, **à version/édition identiques**, y
  compris configuration, `gitlab-secrets.json`, clés SSH/Gitaly et Registry.
  Les UID mappés peuvent différer entre VM : éviter une copie brute non vérifiée.
- Mettre l'ancien Runner en pause, attendre les jobs, l'arrêter, puis migrer son
  ancien dossier `gitlab/gitlab-runner/` vers `runner/config/` sur la VM dédiée,
  ou enregistrer un nouveau Runner. Corriger dans son `config.toml` les anciennes
  URL, `host-gateway`, réseaux, chemins de socket/volumes et caches locaux.
  Ne pas faire tourner simultanément les deux gestionnaires pendant la bascule.
- La rétention interne de trois jours est conservée, mais **ne planifie pas de
  sauvegardes**. Prévoir une sauvegarde GitLab, une sauvegarde séparée des
  configurations/secrets/clés, une copie hors VM et un test de restauration.
- Retour arrière : arrêter les nouveaux services sans `down -v`, restaurer
  l'ancien Compose et sa configuration réseau, puis relancer les anciens
  services sur leurs données conservées. Si des écritures ont eu lieu sur une
  autre VM, organiser leur restauration cohérente avant retour ; ne jamais
  démarrer deux instances GitLab sur les mêmes données.

Les changements de pare-feu, DNS, Traefik et les transferts de données décrits
ici sont des opérations de déploiement à effectuer séparément : ils ne sont
pas appliqués par ce dépôt.
