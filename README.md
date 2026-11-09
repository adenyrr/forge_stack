# forge_stack

Installation neuve sur **deux VM**, avec Docker rootless pour `deploy` (UID 1001) :

- `gitlab-ce/compose.yaml` : GitLab CE, Registry et SSH. Le **NGINX intégré**
  termine TLS avec les certificats de la PKI, renouvelés par `step` sur l'hôte.
- `runner/compose.yaml` : Runner indépendant, exécuteur Docker rootless local.

Les options applicatives initiales sont conservées : SMTP, OIDC Authentik,
signature Gitaly, Registry, optimisations mémoire/disque, logs et limites CPU/RAM.
La gestion des certificats reste sur l'hôte ; aucun client ACME n'est ajouté.

## Images et vérification avant déploiement

Versions stables vérifiées le **9 novembre 2026** auprès de Docker Hub et du
registre `registry-1.docker.io`, avec contrôle des digests SHA-256 des manifestes :

| Image | Version | Digest SHA-256 |
| --- | --- | --- |
| `gitlab/gitlab-ce` | `19.5.3-ce.0` | `6863d156a1a477bd6f69fc674b671d0163cd8b06bf63bb17cc4a4a803b78b27c` |
| `gitlab/gitlab-runner` | `v19.5.3` | `ffe21f021468d4aa1e56f05316d88597c23bf4818840e8edaa858131fabd7991` |
| `alpine` (image de job des exemples) | `3.24.4` | `f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf` |

Ces digests d'index multi-architectures correspondent aux tags officiels `latest`
à la date du contrôle. Les fichiers utilisent des versions et digests fixes,
jamais un tag flottant, une nightly ou une préversion.

**Avant chaque déploiement**, depuis la racine du dépôt :

```sh
python3 scripts/check-images.py
```

Le contrôle vérifie chaque tag épinglé, son digest et sa correspondance avec
`latest`. Il échoue si une référence diffère ou si le registre est inaccessible.
Il ne télécharge pas les couches, ne déploie rien et ne modifie aucun fichier.
En cas d'écart, vérifier la nouvelle version stable et ses notes de version,
puis actualiser ensemble version et digest avant de relancer le contrôle.

## Prérequis rootless

- Docker rootless et Compose v2 installés sur chaque VM pour UID **1001**.
- Exécuter les commandes comme `deploy`, **sans `sudo docker`**, sur son démon
  (`DOCKER_HOST=unix:///run/user/1001/docker.sock` ou contexte `rootless`).
- Socket existant, subuid/subgid configurés, service utilisateur Docker activé
  et lingering activé par un administrateur (`loginctl enable-linger deploy`).
- cgroup v2 avec délégation systemd pour les limites CPU/RAM.
- Stockage compatible et ressources suffisantes : GitLab est limité à 6 Gio et
  2 CPU ; prévoir aussi l'OS et Docker. Le gestionnaire Runner est limité à
  2 Gio et 1 CPU ; les jobs consomment leurs propres ressources.

Vérifier `id -u`, `docker context show`, `docker info` et le socket rootless.
`user: "0:0"` désigne le root **du namespace**, mappé sur UID 1001 de l'hôte.
Ne pas forcer `user: "1001:1001"` ni modifier récursivement les propriétaires
des volumes Omnibus : les services utilisent leurs UID internes mappés.

## GitLab : configuration locale et confidentialité

```sh
cd gitlab-ce
cp .env.example .env
chmod 600 .env
```

Dans `.env`, renseigner :

- `GITLAB_EXTERNAL_URL` et `REGISTRY_EXTERNAL_URL` : URL HTTPS publiques.
- `GITLAB_TLS_CERTIFICATE`, `GITLAB_TLS_KEY`, `REGISTRY_TLS_CERTIFICATE` et
  `REGISTRY_TLS_KEY` : chemins **dans le conteneur**, sous `/etc/gitlab/ssl`.
- `TLS_DIRECTORY` : répertoire existant **sur l'hôte**, monté à cet emplacement.
- Interface privée `HTTPS_BIND_IP`, port SSH, fuseau horaire, SMTP et OIDC.

Le Container Registry est explicitement activé, avec son intégration GitLab et
son hôte virtuel NGINX TLS. Définir par exemple
`REGISTRY_EXTERNAL_URL=https://registry.example.net` dans `.env`, avec un DNS
pointant vers la VM GitLab et un certificat couvrant ce nom.

Les valeurs réelles restent dans `.env`, ignoré par Git ; le fichier versionné
ne contient que des exemples fictifs. Compose transmet les URL et chemins en
variables d'environnement, lues par Omnibus avec `ENV.fetch`, sans valeurs en
dur dans le Compose. Aucun fichier secret supplémentaire n'est nécessaire.
Les variables peuvent aussi être injectées par Komodo.

Il s'agit de ne pas versionner les valeurs, **pas de les rendre secrètes** :
elles restent visibles dans `docker inspect`, `docker compose config` et les
configurations générées. Restreindre les permissions de `.env` (qui contient
aussi des secrets SMTP/OIDC), les sauvegardes et l'accès au démon ; ne pas
publier les sorties contenant ces données. Les valeurs SMTP/OIDC interpolées
dans le Ruby doivent être échappées si elles contiennent apostrophes ou antislashs.

La syntaxe recommandée pour GitLab 19.2+ est
`gitlab_rails['nginx'][...]`, et `registry['nginx'][...]` pour le Registry
([documentation TLS](https://docs.gitlab.com/omnibus/settings/ssl/)).

### Certificats existants et ports

`TLS_DIRECTORY` doit désigner le répertoire **déjà alimenté par step**. Il est
monté en lecture seule, sans création automatique. Un montage du répertoire
permet de voir les remplacements atomiques des fichiers lors du renouvellement.
Les éventuels liens symboliques doivent pointer vers des fichiers accessibles
dans ce montage. Ne monter ni la clé de la CA ni les identifiants du provisioner.

Le compte `deploy` doit pouvoir traverser les répertoires et lire les certificats
et clés privées (par propriétaire/groupe/ACL adaptés, **pas en lecture publique**).
Conserver ces droits après chaque renouvellement. Utiliser une clé sans mot de
passe pour le chargement automatique NGINX. Chaque certificat doit présenter le
SAN correspondant et la chaîne feuille + intermédiaires. Le Registry conserve
un domaine distinct : fournir son certificat, ou une paire dont les SAN couvrent
les deux domaines.

GitLab et Registry écoutent en TLS sur le **443 du conteneur**, avec deux hôtes
virtuels SNI. Le port hôte est **8443** par défaut, compatible rootless.
Faire acheminer le **443 public vers 8443** par le dispositif réseau/hôte prévu,
sans terminaison TLS intermédiaire. Cette règle n'est pas appliquée ici.
Les URL définies dans `.env` restent en `https://…` sans `:8443` dans ce cas.
Si les clients utilisent directement 8443, inclure ce port dans les URL et la
redirection OIDC. Aucun port HTTP ni backend Registry non chiffré n'est publié.
SSH reste sur **2222**, à filtrer séparément.

### Démarrage et renouvellement

Après contrôle des images et préparation des fichiers :

```sh
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
```

Le démarrage Omnibus peut être long (délai initial du healthcheck : 600 s).
La sonde HTTPS locale ignore le certificat uniquement pour tester la disponibilité
sur `localhost` ; elle ne valide ni les SAN ni la confiance PKI. Tester ensuite
les deux URL publiques avec une CA de confiance, **sans `-k`**, puis OIDC, SMTP,
clone Git et push/pull Registry. Préparer aussi la clé de signature Gitaly dans
`/etc/gitlab/gitaly/signing_key` selon le mécanisme de signature retenu.

Le renouvellement de fichiers seul ne recharge pas NGINX. Dans le hook exécuté
**après renouvellement réussi** par le service step de l'hôte, lancer comme
`deploy`, sur le bon démon rootless :

```sh
docker exec gitlab-ce /opt/gitlab/embedded/sbin/nginx -t -c /var/opt/gitlab/nginx/conf/nginx.conf &&
docker exec gitlab-ce gitlab-ctl hup nginx
```

Le même NGINX sert les deux domaines. Configurer l'environnement du hook
(`DOCKER_HOST=unix:///run/user/1001/docker.sock`, utilisateur et PATH) explicitement
si step tourne comme service système. Vérifier le nouveau certificat présenté
sur chacun des domaines et alerter si le renouvellement ou le rechargement échoue.
Le service step existant n'est pas modifié par ce dépôt.

## Runner : installation sur sa propre VM

Aucun port entrant, réseau partagé ni dépendance Compose vers GitLab. Le Runner
et les jobs joignent GitLab par son FQDN HTTPS ; prévoir DNS, routage et sorties
vers GitLab, Registry et dépôts utilisés par les pipelines.

```sh
cd runner
cp .env.example .env
chmod 600 .env
install -d -m 700 config config/certs
```

Placer le certificat public de confiance de la PKI (pas la clé privée) dans
`config/certs/ca.crt`, avant enregistrement. Pour le Registry, installer également
la confiance PKI dans le démon Docker de cette VM ; pour les outils des jobs,
dans leurs images. La confiance du Runner est transmise au helper pour ses
opérations Git/artifacts, mais n'installe pas automatiquement la CA dans toutes
les images utilisateur. Prévoir aussi les CA nécessaires aux connexions sortantes
de GitLab (notamment OIDC) dans `/etc/gitlab/trusted-certs`, puis reconfigurer
Omnibus. Ne jamais désactiver la validation TLS des clients.

Créer un Runner dans GitLab et saisir son jeton d'authentification `glrt-…`
dans un shell Bash, sans l'inscrire dans l'historique :

```bash
read -rsp 'Jeton du runner : ' RUNNER_TOKEN
export RUNNER_TOKEN
read -rp 'URL HTTPS de GitLab : ' CI_SERVER_URL
export CI_SERVER_URL
docker compose config --quiet
docker compose run --rm -e RUNNER_TOKEN -e CI_SERVER_URL gitlab-runner register \
  --non-interactive --name runner-vm --executor docker \
  --tls-ca-file /etc/gitlab-runner/certs/ca.crt \
  --docker-host unix:///var/run/docker.sock \
  --docker-image alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf --docker-volumes /cache
unset RUNNER_TOKEN CI_SERVER_URL
chmod 600 config/config.toml
```

Adapter le fichier généré en suivant `config.toml.example`, en conservant le
vrai jeton : `concurrent = 1`, limites des jobs, `privileged = false` et
`services_privileged = false`. Les limites Compose du gestionnaire ne limitent
pas les conteneurs frères des jobs. Les services auxiliaires consomment aussi
les ressources de la VM.

```sh
docker compose pull
docker compose up -d
docker compose exec gitlab-runner gitlab-runner verify
docker compose ps
```

Valider un pipeline avec clone, artifacts et accès au Registry. Le Runner utilise
directement le socket rootless de sa VM dédiée pour créer/supprimer les conteneurs.
Ce socket donne le contrôle du démon de `deploy` : réserver le Runner à des projets
de confiance, adapter les restrictions de projets/tags et les protections.
Ne monter le socket dans aucun job ; ne pas activer de DinD privilégié par défaut.
`runner/config/`, contenant le jeton et les URL réelles, est exclu de Git.

## Sauvegardes

La rétention interne de trois jours **ne planifie pas de sauvegardes**. Prévoir
les sauvegardes GitLab, Registry, configurations, secrets, clés et données Runner,
une copie hors VM protégée et un test de restauration dès la mise en service.
Les opérations décrites sur DNS, pare-feu, PKI et services de l'hôte restent à
effectuer séparément ; aucun déploiement n'est réalisé par la modification du dépôt.
