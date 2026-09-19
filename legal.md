# Mentions légales — Vigie

Dernière mise à jour : 2026-09-19.

Vigie est un **agrégateur de nouvelles local, gratuit et sans but commercial**,
fait pour la ville de Québec. Vigie ne produit pas d'articles, n'en réécrit
aucun et n'héberge aucune rédaction : il trouve, organise et relaie ce que les
éditeurs publient eux-mêmes, en renvoyant toujours le lecteur vers l'article
original.

## Ce que Vigie relaie, et sur quel fondement

Pour chaque article, Vigie affiche :

- le **titre**, relayé tel quel (tronqué, jamais réécrit, jamais complété) ;
- un **extrait court** (240 caractères ou moins) du résumé que l'éditeur
  publie lui-même dans son flux RSS, toujours étiqueté « Extrait du flux de
  {source} » ;
- la **source, l'auteur quand le flux le donne, la date de publication**, et un
  **lien direct vers l'article original** chez l'éditeur ;
- éventuellement une **image d'aperçu fournie par l'éditeur lui-même**
  (balise og:image de l'article ou média attaché à son propre flux), sans
  aucune modification, avec la mention « Photo : {source} » (et le crédit
  photographe quand l'éditeur l'attache à cette image).

Ces usages reposent sur l'**utilisation équitable aux fins de reportage
d'actualité** (Loi sur le droit d'auteur, art. 29 et 29.2 ; CCH c. Law Society,
2004 CSC 13), qui exige la mention de la source et, lorsqu'elle est donnée, du
nom de l'auteur — c'est exactement ce que la signature de chaque article
affiche. Les liens vers les articles originaux s'appuient sur Crookes c.
Newton, 2011 CSC 47 : un hyperlien n'est pas une publication du contenu lié.
Les données de travaux routiers de la Ville de Québec sont relayées sous
licence **CC-BY 4.0** avec attribution (via Données Québec), conformément à
cette licence. Les citations courtes affichées dans les dossiers proposés
(≤ 200 caractères, attribuées à leur locuteur) relèvent du droit de citation.

Vigie ne reproduit **jamais** le corps des articles, ne revend rien, ne
distribue aucun flux à des tiers et n'entraîne aucun modèle sur les contenus.

## Statut non commercial — engagement du fondateur

Décision du fondateur (2026-09-19) : **Vigie reste gratuit et non commercial.**
Pas de publicité, pas d'abonnement payant, pas de revente de données, pas de
commandite. Aucun compte lecteur : les articles gardés et les repères de
lecture restent dans le navigateur du visiteur. Si ce statut devait changer un
jour, les autorisations des éditeurs concernés seraient obtenues **avant**
tout changement, et cette page serait mise à jour.

## Identité de collecte

La collecte des flux et des images s'identifie honnêtement :

```
Vigie/0.2 (+https://vigieqc.com/legal.md; news aggregator; non-commercial)
```

Certains serveurs ralentissent ou interrompent les lecteurs automatisés au
niveau du transport (sans refus HTTP) ; pour ceux-là uniquement, et c'est
documenté ici, Vigie utilise une identité de navigateur standard :

```
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Vigie/0.2
```

Le changement d'identité n'a lieu qu'après un échec de transport réel (délai,
connexion réinitialisée), jamais après un refus HTTP, et il expire après
30 jours : l'identité honnête est alors réessayée. **Un refus
HTTP (403, 410, 429…) est toujours respecté** : il n'est jamais contourné, ni
par une autre identité, ni par un autre chemin. Aucun mur de paiement n'est
jamais touché. La collecte est conditionnelle (ETag / If-Modified-Since) pour
ne transférer que ce qui a changé, et chaque silence est diagnostiqué plutôt
que comblé.

## Conservation

Les instantanés de flux bruts sont supprimés après **30 jours**. Les images
d'aperçu sont supprimées dès qu'un article sort du périmètre du point local.
Les journaux internes ne contiennent aucune donnée de lecteur.

## Retrait et contact

Un éditeur qui souhaite que Vigie cesse de relayer son flux, un article ou une
image est retiré **dans l'heure qui suit la demande** (une édition), et la
source est désactivée publiquement dans `sources.yaml` avec la raison de la
coupure — jamais silencieusement.

Contact : dépôt public [github.com/Ombo1601/vigie](https://github.com/Ombo1601/vigie)
(section Issues), ou l'adresse qui y figure.

## Responsabilité

Vigie relaie les titres et extraits des éditeurs ; les opinions et faits
appartiennent à leurs auteurs. Les rapprochements d'articles en « dossiers »
sont des **propositions automatiques à vérifier**, affichées comme telles :
plusieurs médias ne constituent pas plusieurs confirmations indépendantes, et
Vigie ne couronne jamais une réponse. La liste complète des sources est
publique : [sources.yaml](/sources.yaml). La méthode de classement est
publique : [ranking.md](/ranking.md).
