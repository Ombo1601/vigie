# Mentions légales — Vigie

Dernière mise à jour : 2026-10-05.

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

L'auteur est affiché à côté du titre relayé sur les pages de
Vigie (le point, les dossiers, l'affiche et les fichiers pour machines) : « Par
{auteur} » quand le flux de l'éditeur le donne. Les fichiers pour machines
(`/index.html.md`, `/delta/latest.json`, `/llms.txt`) ne portent que l'éditeur,
l'auteur, le titre et le lien — jamais d'extrait — et rappellent que les titres
appartiennent à leurs éditeurs.

Vigie ne reproduit **jamais** le corps des articles, ne revend rien, ne
distribue aucun flux à des tiers et n'entraîne aucun modèle sur les contenus.

## Statut non commercial — engagement public de Vigie

Engagement public (2026-09-19) : **Vigie reste gratuit et non commercial.**
Pas de publicité, pas d'abonnement payant, pas de revente de données, pas de
commandite. Aucun compte lecteur : les articles gardés et les repères de
lecture restent dans le navigateur du visiteur. Si ce statut devait changer un
jour, les autorisations des éditeurs concernés seraient obtenues **avant**
tout changement, et cette page serait mise à jour.

## Identité de collecte

Toute la collecte (flux, pages d'articles, images, fichiers robots.txt)
s'identifie d'une seule façon, honnêtement :

```
Vigie/0.2 (+https://vigieqc.com/methode/legal.html; news aggregator; non-commercial)
```

Vigie n'emprunte aucune autre identité : ni identité de navigateur, ni en-tête
Referer imitant une visite sur le site de l'éditeur. Un serveur qui ne répond
pas à cette identité n'est pas contourné : la collecte manquée est consignée
comme une lacune de collecte de Vigie, jamais comme un silence de l'institution.

**Un refus HTTP (403, 410, 429…) est toujours respecté** : il met fin à la
collecte de ce flux pour cette édition, sans nouvelle tentative, ni par une
autre identité, ni par un autre chemin. Seul un serveur qui ne donne aucune
réponse (délai dépassé, connexion coupée) peut être relu à une autre adresse
que l'éditeur publie pour le même flux, avec la même identité.

Avant de lire une page d'article ou une image, Vigie consulte le fichier
robots.txt du site (une fois par collecte) et respecte ses interdictions
visant Vigie ou tous les robots. Si ce fichier est absent (404), la lecture est
permise ; s'il ne peut pas être lu pour toute autre raison, la page ou l'image
n'est pas lue, et la raison est consignée.

Aucun mur de paiement n'est jamais touché. La collecte est conditionnelle
(ETag / If-Modified-Since) pour ne transférer que ce qui a changé, et chaque
silence est diagnostiqué plutôt que comblé.

## Conservation

Les instantanés de flux bruts sont supprimés après **30 jours**. Les images
d'aperçu sont supprimées dès qu'un article sort du périmètre du point local.
Les journaux internes ne contiennent aucune donnée de lecteur.

## Retrait et contact

Un éditeur ou un ayant droit qui souhaite que Vigie cesse de relayer son flux,
son domaine, un article ou une image obtient le retrait **le jour même, au plus
tard à l'édition suivante (environ 6 h)** : la demande est inscrite au registre
public des retraits (`takedowns.yaml`) et appliquée dans ce délai. Dès lors,
l'élément n'entre plus dans aucune édition ni aucune page ; un flux ou un
domaine retiré n'est plus collecté ; les images d'aperçu concernées sont
supprimées de ce site et ne sont plus récupérées ; une mise en ligne qui
contiendrait encore un élément retiré est refusée. Les instantanés bruts d'une
source retirée sont supprimés au même moment.

La liste des retraits — éditeur, portée et date, jamais le contenu retiré —
est publiée sur la [page des sources](/methode/sources.html#retraits), où une source retirée
reste nommée, avec la mention de son retrait à la demande de l'éditeur : jamais
silencieusement.
Les sceaux déjà publiés du registre ne contiennent aucun texte d'éditeur
(identifiants et comptes seulement) et restent intacts.

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
