"""Bilingual French/English lexicon of high-value Québec / Canada terms.

Phase 0 evaluation asset (stdlib only). Every entry is one real-world thing
(a place, an institution, a party, a project, an event type or a role) with
its French and English surface forms. The graded matcher folds a headline
(lowercase, accents stripped, punctuation to spaces) and finds the longest
surface at each position, so "Université Laval" never reads as the city of
Laval and "Laval University" lands on the same id as "Université Laval".

This file holds vocabulary, never publisher text.

Kinds and what they are allowed to prove:
  place        a named neighbourhood, town, landmark or venue (strong)
  road         a named road, bridge or highway (strong; different roads conflict)
  broad        a city, province or country (weak: almost every item has one)
  institution  a public body, court, hospital, police force, utility, club
  party        a political party (weak: a party is an actor, not an event)
  project      a named public project (tramway, third link)
  event        an event type (fire, strike, trial): a shared type is weak but
               useful; it is mostly a bilingual bridge for token overlap
  role         a public role (mayor, premier); weakest
"""
from __future__ import annotations

import re
import unicodedata

# (canonical id, kind, French surfaces, English surfaces)
# The first French surface is the display label.
ENTRIES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    # ---- broad places -------------------------------------------------------
    ("quebec-city", "broad", ("Ville de Québec",), ("Quebec City", "City of Quebec")),
    ("quebec", "broad", ("Québec", "province de Québec", "le Québec"), ("Quebec", "province of Quebec")),
    ("canada", "broad", ("Canada",), ("Canada",)),
    ("capitale-nationale", "broad", ("Capitale-Nationale", "région de Québec"), ("Quebec City region", "Capitale-Nationale region")),
    ("chaudiere-appalaches", "broad", ("Chaudière-Appalaches",), ("Chaudiere-Appalaches",)),
    ("etats-unis", "broad", ("États-Unis", "américain", "américaine"), ("United States", "U.S.", "American")),
    ("union-europeenne", "broad", ("Union européenne", "UE"), ("European Union",)),  # not bare "EU": folds to French "eu"
    ("otan", "broad", ("OTAN",), ("NATO",)),
    ("montreal", "place", ("Montréal",), ("Montreal",)),
    ("ottawa", "broad", ("Ottawa",), ("Ottawa",)),
    ("toronto", "place", ("Toronto",), ("Toronto",)),
    ("laval-ville", "place", ("Laval",), ("Laval",)),
    ("longueuil", "place", ("Longueuil",), ("Longueuil",)),
    ("gatineau", "place", ("Gatineau",), ("Gatineau",)),
    ("sherbrooke", "place", ("Sherbrooke",), ("Sherbrooke",)),
    ("trois-rivieres", "place", ("Trois-Rivières",), ("Trois-Rivieres",)),
    ("saguenay", "place", ("Saguenay", "Chicoutimi", "Jonquière"), ("Saguenay", "Chicoutimi")),
    ("rimouski", "place", ("Rimouski",), ("Rimouski",)),
    ("drummondville", "place", ("Drummondville",), ("Drummondville",)),
    ("riviere-du-loup", "place", ("Rivière-du-Loup",), ("Riviere-du-Loup",)),
    ("thetford-mines", "place", ("Thetford Mines",), ("Thetford Mines",)),
    ("beauce", "place", ("Beauce", "Saint-Georges-de-Beauce"), ("Beauce",)),
    ("charlevoix", "place", ("Charlevoix", "Baie-Saint-Paul", "La Malbaie"), ("Charlevoix", "Baie-Saint-Paul")),
    ("portneuf", "place", ("Portneuf",), ("Portneuf",)),
    ("montmagny", "place", ("Montmagny",), ("Montmagny",)),
    ("cote-nord", "place", ("Côte-Nord", "Sept-Îles", "Baie-Comeau"), ("North Shore", "Sept-Iles", "Baie-Comeau")),
    ("gaspesie", "place", ("Gaspésie",), ("Gaspe Peninsula", "Gaspesie")),
    ("abitibi", "place", ("Abitibi", "Rouyn-Noranda", "Val-d'Or"), ("Abitibi", "Rouyn-Noranda", "Val-d'Or")),
    # ---- Québec City and Lévis: boroughs, neighbourhoods, suburbs ----------
    ("levis", "place", ("Lévis",), ("Levis",)),
    ("limoilou", "place", ("Limoilou",), ("Limoilou",)),
    ("saint-roch", "place", ("Saint-Roch", "St-Roch"), ("St. Roch", "Saint-Roch")),
    ("saint-sauveur", "place", ("Saint-Sauveur", "St-Sauveur"), ("St. Sauveur", "Saint-Sauveur")),
    ("saint-jean-baptiste", "place", ("Saint-Jean-Baptiste", "faubourg Saint-Jean"), ("Saint-Jean-Baptiste",)),
    ("montcalm", "place", ("Montcalm",), ("Montcalm",)),
    ("vieux-quebec", "place", ("Vieux-Québec", "Vieux Québec"), ("Old Quebec", "Old Québec")),
    ("vieux-port", "place", ("Vieux-Port", "Vieux-Port de Québec"), ("Old Port",)),
    ("petit-champlain", "place", ("Petit-Champlain", "Quartier Petit Champlain"), ("Petit Champlain",)),
    ("beauport", "place", ("Beauport",), ("Beauport",)),
    ("charlesbourg", "place", ("Charlesbourg",), ("Charlesbourg",)),
    ("sainte-foy", "place", ("Sainte-Foy", "Ste-Foy"), ("Sainte-Foy", "Ste-Foy")),
    ("sillery", "place", ("Sillery",), ("Sillery",)),
    ("cap-rouge", "place", ("Cap-Rouge",), ("Cap-Rouge",)),
    ("loretteville", "place", ("Loretteville",), ("Loretteville",)),
    ("val-belair", "place", ("Val-Bélair",), ("Val-Belair",)),
    ("lebourgneuf", "place", ("Lebourgneuf",), ("Lebourgneuf",)),
    ("duberger", "place", ("Duberger", "Les Saules"), ("Duberger",)),
    ("vanier", "place", ("Vanier",), ("Vanier",)),
    ("neufchatel", "place", ("Neufchâtel",), ("Neufchatel",)),
    ("lac-saint-charles", "place", ("Lac-Saint-Charles",), ("Lac-Saint-Charles",)),
    ("saint-emile", "place", ("Saint-Émile",), ("Saint-Emile",)),
    ("maizerets", "place", ("Maizerets",), ("Maizerets",)),
    ("lairet", "place", ("Lairet",), ("Lairet",)),
    ("haute-saint-charles", "place", ("La Haute-Saint-Charles", "Haute-Saint-Charles"), ("Haute-Saint-Charles",)),
    ("la-cite", "place", ("La Cité-Limoilou", "Cité-Limoilou"), ("La Cite-Limoilou",)),
    ("les-rivieres", "place", ("Les Rivières",), ("Les Rivieres",)),
    ("wendake", "place", ("Wendake",), ("Wendake",)),
    ("ancienne-lorette", "place", ("L'Ancienne-Lorette", "Ancienne-Lorette"), ("L'Ancienne-Lorette",)),
    ("saint-augustin", "place", ("Saint-Augustin-de-Desmaures", "Saint-Augustin"), ("Saint-Augustin-de-Desmaures",)),
    ("stoneham", "place", ("Stoneham", "Stoneham-et-Tewkesbury"), ("Stoneham",)),
    ("lac-beauport", "place", ("Lac-Beauport",), ("Lac-Beauport",)),
    ("boischatel", "place", ("Boischatel",), ("Boischatel",)),
    ("ile-d-orleans", "place", ("Île d'Orléans", "île d'Orléans"), ("Ile d'Orleans", "Orleans Island")),
    ("cote-de-beaupre", "place", ("Côte-de-Beaupré", "Beaupré", "Château-Richer"), ("Cote-de-Beaupre", "Beaupre")),
    ("mont-sainte-anne", "place", ("Mont-Sainte-Anne",), ("Mont-Sainte-Anne",)),
    ("sainte-anne-de-beaupre", "place", ("Sainte-Anne-de-Beaupré",), ("Sainte-Anne-de-Beaupre",)),
    ("saint-nicolas", "place", ("Saint-Nicolas",), ("Saint-Nicolas",)),
    ("charny", "place", ("Charny",), ("Charny",)),
    ("saint-romuald", "place", ("Saint-Romuald",), ("Saint-Romuald",)),
    ("pintendre", "place", ("Pintendre",), ("Pintendre",)),
    ("saint-lambert-de-lauzon", "place", ("Saint-Lambert-de-Lauzon",), ("Saint-Lambert-de-Lauzon",)),
    ("valcartier", "place", ("Valcartier", "base de Valcartier"), ("Valcartier", "CFB Valcartier")),
    ("shannon", "place", ("Shannon",), ("Shannon",)),
    # ---- landmarks and venues ----------------------------------------------
    ("plaines-abraham", "place", ("plaines d'Abraham",), ("Plains of Abraham",)),
    ("chateau-frontenac", "place", ("Château Frontenac", "Château-Frontenac"), ("Chateau Frontenac",)),
    ("colline-parlementaire", "place", ("colline Parlementaire", "Hôtel du Parlement"), ("Quebec legislature",)),
    ("centre-videotron", "place", ("Centre Vidéotron",), ("Videotron Centre",)),
    ("expocite", "place", ("ExpoCité",), ("ExpoCite",)),
    ("baie-de-beauport", "place", ("baie de Beauport",), ("Beauport Bay",)),
    ("chute-montmorency", "place", ("chute Montmorency", "chute-Montmorency"), ("Montmorency Falls",)),
    ("fleuve-saint-laurent", "place", ("fleuve Saint-Laurent",), ("St. Lawrence River",)),
    ("riviere-saint-charles", "place", ("rivière Saint-Charles",), ("St. Charles River", "Saint-Charles River")),
    ("aeroport-jean-lesage", "place", ("aéroport Jean-Lesage", "aéroport de Québec"), ("Jean Lesage airport", "Quebec City airport")),
    ("port-de-quebec", "place", ("port de Québec",), ("Port of Quebec",)),
    ("galeries-de-la-capitale", "place", ("Galeries de la Capitale",), ("Galeries de la Capitale",)),
    ("place-laurier", "place", ("Place Laurier", "Laurier Québec"), ("Laurier Quebec",)),
    ("grand-theatre", "place", ("Grand Théâtre de Québec", "Grand Théâtre"), ("Grand Theatre",)),
    ("parc-victoria", "place", ("parc Victoria",), ("Victoria Park",)),
    ("domaine-de-maizerets", "place", ("Domaine de Maizerets",), ("Domaine de Maizerets",)),
    # ---- roads, bridges, crossings ------------------------------------------
    ("pont-pierre-laporte", "road", ("pont Pierre-Laporte",), ("Pierre Laporte Bridge", "Pierre-Laporte bridge")),
    ("pont-de-quebec", "road", ("pont de Québec",), ("Quebec Bridge",)),
    ("traversier-quebec-levis", "road", ("traversier Québec-Lévis", "traverse Québec-Lévis"), ("Quebec-Levis ferry",)),
    ("autoroute-40", "road", ("autoroute 40", "A-40", "autoroute Félix-Leclerc"), ("Highway 40", "Autoroute 40")),
    ("autoroute-20", "road", ("autoroute 20", "A-20", "autoroute Jean-Lesage"), ("Highway 20", "Autoroute 20")),
    ("autoroute-73", "road", ("autoroute 73", "A-73", "autoroute Laurentienne"), ("Highway 73", "Autoroute 73")),
    ("autoroute-henri-iv", "road", ("autoroute Henri-IV",), ("Henri-IV highway", "Henri IV highway")),
    ("autoroute-dufferin", "road", ("autoroute Dufferin-Montmorency", "autoroute Dufferin"), ("Dufferin-Montmorency highway", "Dufferin highway")),
    ("autoroute-robert-bourassa", "road", ("autoroute Robert-Bourassa",), ("Robert-Bourassa highway",)),
    ("autoroute-du-vallon", "road", ("autoroute Du Vallon", "autoroute du Vallon"), ("Du Vallon highway",)),
    ("route-138", "road", ("route 138",), ("Route 138", "Highway 138")),
    ("route-175", "road", ("route 175",), ("Route 175", "Highway 175")),
    ("route-169", "road", ("route 169",), ("Route 169", "Highway 169")),
    ("boulevard-laurier", "road", ("boulevard Laurier",), ("Laurier Boulevard",)),
    ("boulevard-charest", "road", ("boulevard Charest",), ("Charest Boulevard",)),
    ("boulevard-hamel", "road", ("boulevard Hamel",), ("Hamel Boulevard",)),
    ("boulevard-sainte-anne", "road", ("boulevard Sainte-Anne",), ("Sainte-Anne Boulevard",)),
    ("boulevard-rene-levesque", "road", ("boulevard René-Lévesque",), ("Rene-Levesque Boulevard",)),
    ("boulevard-wilfrid-hamel", "road", ("boulevard Wilfrid-Hamel",), ("Wilfrid-Hamel Boulevard",)),
    ("grande-allee", "road", ("Grande Allée",), ("Grande Allee",)),
    ("rue-saint-jean", "road", ("rue Saint-Jean",), ("Saint-Jean Street", "St. Jean Street")),
    ("rue-saint-joseph", "road", ("rue Saint-Joseph",), ("Saint-Joseph Street",)),
    ("rue-cartier", "road", ("avenue Cartier",), ("Cartier Avenue",)),
    ("chemin-sainte-foy", "road", ("chemin Sainte-Foy",), ("chemin Sainte-Foy",)),
    ("boulevard-guillaume-couture", "road", ("boulevard Guillaume-Couture",), ("Guillaume-Couture Boulevard",)),
    # ---- public institutions --------------------------------------------------
    ("assemblee-nationale", "institution", ("Assemblée nationale",), ("National Assembly",)),
    ("hotel-de-ville", "institution", ("hôtel de ville", "conseil municipal"), ("city hall", "city council")),
    ("spvq", "institution", ("SPVQ", "Service de police de la Ville de Québec", "police de Québec"), ("Quebec City police",)),
    ("surete-du-quebec", "institution", ("Sûreté du Québec", "SQ"), ("Sûreté du Québec", "Quebec provincial police", "provincial police")),
    ("grc", "institution", ("GRC", "Gendarmerie royale du Canada"), ("RCMP", "Royal Canadian Mounted Police")),
    ("spvm", "institution", ("SPVM",), ("Montreal police",)),
    ("police-levis", "institution", ("Service de police de la Ville de Lévis", "police de Lévis"), ("Levis police",)),
    ("bec", "institution", ("Bureau des enquêtes indépendantes", "BEI"), ("independent investigations bureau", "BEI")),
    ("coroner", "institution", ("Bureau du coroner", "coroner", "coroners"), ("coroner", "coroner's office")),
    ("dpcp", "institution", ("DPCP", "Directeur des poursuites criminelles et pénales"), ("Crown prosecutor", "Crown prosecutors")),
    ("palais-de-justice", "institution", ("palais de justice",), ("courthouse",)),
    ("cour-du-quebec", "institution", ("Cour du Québec",), ("Court of Quebec",)),
    ("cour-superieure", "institution", ("Cour supérieure",), ("Superior Court",)),
    ("cour-d-appel", "institution", ("Cour d'appel",), ("Court of Appeal",)),
    ("cour-supreme", "institution", ("Cour suprême",), ("Supreme Court",)),
    ("tal", "institution", ("Tribunal administratif du logement", "TAL"), ("housing tribunal", "rental board")),
    ("cnesst", "institution", ("CNESST",), ("CNESST", "workplace safety board")),
    ("chu-de-quebec", "institution", ("CHU de Québec", "CHU de Québec-Université Laval", "CHUL"), ("CHU de Quebec", "Quebec City university hospital")),
    ("enfant-jesus", "institution", ("hôpital de l'Enfant-Jésus", "Enfant-Jésus"), ("Enfant-Jesus hospital",)),
    ("hotel-dieu-de-quebec", "institution", ("Hôtel-Dieu de Québec",), ("Hotel-Dieu de Quebec",)),
    ("saint-francois-d-assise", "institution", ("hôpital Saint-François d'Assise", "Saint-François d'Assise"), ("Saint-Francois d'Assise hospital",)),
    ("iucpq", "institution", ("IUCPQ", "Institut universitaire de cardiologie et de pneumologie de Québec", "hôpital Laval"), ("Quebec Heart and Lung Institute",)),
    ("hotel-dieu-de-levis", "institution", ("Hôtel-Dieu de Lévis",), ("Hotel-Dieu de Levis",)),
    ("ciusss-capitale-nationale", "institution", ("CIUSSS de la Capitale-Nationale", "CIUSSS"), ("CIUSSS de la Capitale-Nationale",)),
    ("cisss-chaudiere-appalaches", "institution", ("CISSS de Chaudière-Appalaches", "CISSS"), ("CISSS de Chaudiere-Appalaches",)),
    ("sante-quebec", "institution", ("Santé Québec",), ("Sante Quebec", "Health Quebec")),
    ("universite-laval", "institution", ("Université Laval",), ("Laval University", "Universite Laval")),
    ("rtc", "institution", ("RTC", "Réseau de transport de la Capitale"), ("RTC", "Quebec City transit")),
    ("stlevis", "institution", ("STLévis", "Société de transport de Lévis"), ("STLevis", "Levis transit")),
    ("hydro-quebec", "institution", ("Hydro-Québec",), ("Hydro-Quebec", "Hydro Quebec")),
    ("saaq", "institution", ("SAAQ", "Société de l'assurance automobile du Québec"), ("SAAQ", "Quebec auto insurance board")),
    ("mtq", "institution", ("ministère des Transports", "Transports Québec", "MTQ", "Mobilité Infra Québec"), ("Transport Ministry", "Transports Quebec")),
    ("ministere-education", "institution", ("ministère de l'Éducation", "centre de services scolaire", "CSS"), ("Education Ministry", "school service centre")),
    ("ministere-sante", "institution", ("ministère de la Santé",), ("Health Ministry",)),
    ("gouvernement-quebec", "institution", ("gouvernement du Québec", "gouvernement Legault"), ("Quebec government", "Legault government")),
    ("gouvernement-federal", "institution", ("gouvernement fédéral",), ("federal government",)),
    ("chambre-des-communes", "institution", ("Chambre des communes", "Parlement fédéral"), ("House of Commons",)),
    ("senat", "institution", ("Sénat",), ("Senate",)),
    ("environnement-canada", "institution", ("Environnement Canada",), ("Environment Canada",)),
    ("postes-canada", "institution", ("Postes Canada",), ("Canada Post",)),
    ("via-rail", "institution", ("Via Rail", "VIA Rail"), ("Via Rail",)),
    ("banque-du-canada", "institution", ("Banque du Canada",), ("Bank of Canada",)),
    ("statistique-canada", "institution", ("Statistique Canada",), ("Statistics Canada", "StatCan")),
    ("isq", "institution", ("Institut de la statistique du Québec",), ("Institut de la statistique du Quebec",)),
    ("elections-quebec", "institution", ("Élections Québec",), ("Elections Quebec",)),
    ("elections-canada", "institution", ("Élections Canada",), ("Elections Canada",)),
    ("communaute-metropolitaine", "institution", ("Communauté métropolitaine de Québec", "CMQ"), ("Quebec Metropolitan Community",)),
    ("ombudsman", "institution", ("Protecteur du citoyen",), ("Quebec ombudsman",)),
    ("verificatrice-generale", "institution", ("vérificatrice générale", "vérificateur général"), ("auditor general",)),
    ("ftq", "institution", ("FTQ",), ("FTQ",)),
    ("csn", "institution", ("CSN",), ("CSN",)),
    ("csq", "institution", ("CSQ",), ("CSQ",)),
    ("fae", "institution", ("FAE", "Fédération autonome de l'enseignement"), ("FAE",)),
    ("fiq", "institution", ("FIQ",), ("FIQ", "nurses union")),
    ("front-commun", "institution", ("Front commun",), ("Common Front",)),
    ("desjardins", "institution", ("Desjardins",), ("Desjardins",)),
    ("remparts", "institution", ("Remparts", "Remparts de Québec"), ("Quebec Remparts", "Remparts")),
    ("nordiques", "institution", ("Nordiques",), ("Nordiques",)),
    ("rouge-et-or", "institution", ("Rouge et Or",), ("Rouge et Or",)),
    ("capitales", "institution", ("Capitales de Québec",), ("Quebec Capitales",)),
    ("carnaval", "institution", ("Carnaval de Québec", "Bonhomme"), ("Quebec Winter Carnival", "Winter Carnival")),
    ("feq", "institution", ("Festival d'été de Québec", "Festival d'été", "FEQ"), ("Quebec City Summer Festival", "Festival d'ete")),
    ("fetes-de-la-nouvelle-france", "institution", ("Fêtes de la Nouvelle-France",), ("New France Festival",)),
    # ---- political parties ---------------------------------------------------
    ("caq", "party", ("CAQ", "Coalition avenir Québec", "caquiste", "caquistes"), ("CAQ", "Coalition Avenir Quebec")),
    ("pq", "party", ("PQ", "Parti québécois", "péquiste", "péquistes"), ("Parti Quebecois", "PQ")),
    ("plq", "party", ("PLQ", "Parti libéral du Québec", "libéraux du Québec"), ("Quebec Liberals", "Quebec Liberal Party")),
    ("qs", "party", ("QS", "Québec solidaire"), ("Quebec solidaire",)),
    ("pcq", "party", ("PCQ", "Parti conservateur du Québec"), ("Conservative Party of Quebec", "Quebec Conservatives")),
    ("plc", "party", ("PLC", "Parti libéral du Canada", "libéraux fédéraux"), ("Liberal Party of Canada", "federal Liberals")),
    ("pcc", "party", ("PCC", "Parti conservateur du Canada", "conservateurs fédéraux"), ("Conservative Party of Canada", "federal Conservatives")),
    ("npd", "party", ("NPD", "néo-démocrates"), ("NDP", "New Democrats")),
    ("bloc", "party", ("Bloc québécois", "bloquiste"), ("Bloc Quebecois",)),
    ("quebec-forte-et-fiere", "party", ("Québec forte et fière",), ("Quebec forte et fiere",)),
    ("leadership-quebec", "party", ("Leadership Québec",), ("Leadership Quebec",)),
    ("transition-quebec", "party", ("Transition Québec",), ("Transition Quebec",)),
    ("respect-citoyens", "party", ("Respect citoyens",), ("Respect citoyens",)),
    # ---- named public projects ---------------------------------------------
    ("tramway", "project", ("tramway", "tramway de Québec"), ("tramway", "tram", "Quebec City tramway")),
    ("troisieme-lien", "project", ("troisième lien", "3e lien", "tunnel Québec-Lévis"), ("third link", "third-link", "Quebec-Levis tunnel")),
    ("nouvel-hopital", "project", ("nouvel hôpital de l'Enfant-Jésus", "nouveau complexe hospitalier"), ("new Enfant-Jesus hospital",)),
    ("ecoquartier", "project", ("écoquartier",), ("eco-district", "ecodistrict")),
    ("trambus", "project", ("trambus", "Trambus"), ("trambus",)),
    ("privatisation-aeroports", "project", ("privatisation des aéroports",), ("airport privatization",)),
    # ---- event types -----------------------------------------------------------
    ("ev-incendie", "event", ("incendie", "incendies", "brasier", "flammes", "pompiers"), ("fire", "fires", "blaze", "firefighters")),
    ("ev-feu-de-foret", "event", ("feu de forêt", "feux de forêt", "SOPFEU"), ("wildfire", "wildfires", "forest fire")),
    ("ev-collision", "event", ("collision", "accident", "accident routier", "carambolage", "collision frontale"), ("collision", "crash", "car crash", "pileup", "head-on collision")),
    ("ev-ecrasement", "event", ("écrasement",), ("plane crash", "aircraft crash")),
    ("ev-deraillement", "event", ("déraillement",), ("derailment",)),
    ("ev-delit-de-fuite", "event", ("délit de fuite",), ("hit-and-run", "hit and run")),
    ("ev-pieton", "event", ("piéton", "piétonne", "piétons"), ("pedestrian", "pedestrians")),
    ("ev-cycliste", "event", ("cycliste", "cyclistes"), ("cyclist", "cyclists")),
    ("ev-noyade", "event", ("noyade", "noyé", "noyée"), ("drowning", "drowned")),
    ("ev-homicide", "event", ("meurtre", "homicide", "meurtrier"), ("murder", "homicide", "killing")),
    ("ev-fusillade", "event", ("fusillade", "coups de feu", "par balle"), ("shooting", "gunfire", "shots fired")),
    ("ev-poignardage", "event", ("poignardé", "poignardée", "arme blanche", "coups de couteau"), ("stabbing", "stabbed")),
    ("ev-agression", "event", ("agression", "agressions sexuelles", "agression sexuelle", "voies de fait"), ("assault", "sexual assault")),
    ("ev-arrestation", "event", ("arrestation", "arrêté", "arrêtée", "arrêtés", "interpellé"), ("arrest", "arrested")),
    ("ev-accusation", "event", ("accusé", "accusée", "accusés", "accusations", "inculpé"), ("charged", "charges", "accused")),
    ("ev-proces", "event", ("procès",), ("trial",)),
    ("ev-peine", "event", ("peine de prison", "condamné", "condamnée", "prison", "pénitencier"), ("sentence", "sentenced", "jail", "prison", "penitentiary")),
    ("ev-verdict", "event", ("verdict", "coupable", "acquitté", "acquittée"), ("verdict", "guilty", "acquitted")),
    ("ev-enquete", "event", ("enquête", "enquêtes"), ("investigation", "inquiry", "probe")),
    ("ev-disparition", "event", ("disparition", "disparu", "disparue"), ("missing", "disappearance")),
    ("ev-deces", "event", ("décès", "mort", "morte", "morts", "décédé", "décédée", "sans vie"), ("death", "dead", "died", "dies", "killed")),
    ("ev-blesse", "event", ("blessé", "blessée", "blessés", "blessures"), ("injured", "injuries", "hurt")),
    ("ev-evacuation", "event", ("évacuation", "évacués", "évacuées"), ("evacuation", "evacuated")),
    ("ev-explosion", "event", ("explosion",), ("explosion", "blast")),
    ("ev-greve", "event", ("grève", "grèves", "grévistes", "débrayage"), ("strike", "strikes", "walkout")),
    ("ev-lockout", "event", ("lock-out", "lockout"), ("lockout", "lock-out")),
    ("ev-negociation", "event", ("négociations", "convention collective", "entente de principe"), ("negotiations", "collective agreement", "tentative agreement")),
    ("ev-manifestation", "event", ("manifestation", "manifestants", "manif"), ("protest", "protesters", "demonstration")),
    ("ev-election", "event", ("élection", "élections", "scrutin", "électorale", "électoral"), ("election", "elections", "vote", "ballot")),
    ("ev-sondage", "event", ("sondage", "sondages"), ("poll", "polls", "survey")),
    ("ev-debat", "event", ("débat", "débat des chefs"), ("debate", "leaders debate")),
    ("ev-budget", "event", ("budget", "budgétaire", "mise à jour économique"), ("budget", "economic update")),
    ("ev-taxes", "event", ("taxes foncières", "compte de taxes", "hausse de taxes", "impôt", "impôts"), ("property tax", "tax bill", "tax hike", "taxes")),
    ("ev-tarifs", "event", ("tarifs", "droits de douane"), ("tariff", "tariffs")),
    ("ev-tempete", "event", ("tempête", "tempêtes", "bordée"), ("storm", "snowstorm")),
    ("ev-neige", "event", ("neige", "déneigement", "chute de neige"), ("snow", "snowfall", "snow removal")),
    ("ev-verglas", "event", ("verglas", "pluie verglaçante"), ("freezing rain", "ice storm")),
    ("ev-inondation", "event", ("inondation", "inondations", "crue", "crues"), ("flood", "floods", "flooding")),
    ("ev-canicule", "event", ("canicule", "chaleur extrême"), ("heat wave", "heatwave", "extreme heat")),
    ("ev-panne", "event", ("panne", "pannes", "panne de courant"), ("outage", "power outage", "blackout")),
    ("ev-fermeture", "event", ("fermeture", "fermée", "fermé", "entrave", "entraves"), ("closure", "closed", "shut down")),
    ("ev-travaux", "event", ("travaux", "chantier", "chantiers", "détour"), ("roadwork", "construction", "detour")),
    ("ev-ebullition", "event", ("avis d'ébullition", "ébullition"), ("boil-water advisory", "boil water advisory")),
    ("ev-alerte-amber", "event", ("alerte Amber",), ("Amber Alert",)),
    ("ev-fraude", "event", ("fraude", "fraudes", "arnaque"), ("fraud", "scam")),
    ("ev-perquisition", "event", ("perquisition", "perquisitions", "saisie"), ("raid", "search warrant", "seizure")),
    ("ev-drogue", "event", ("drogue", "drogues", "stupéfiants", "cannabis", "fentanyl"), ("drug", "drugs", "narcotics", "cannabis", "fentanyl")),
    ("ev-demission", "event", ("démission", "démissionne", "quitte ses fonctions"), ("resignation", "resigns", "steps down")),
    ("ev-nomination", "event", ("nomination",), ("appointment", "appointed")),
    ("ev-poursuite", "event", ("poursuite", "poursuit", "recours collectif", "action collective"), ("lawsuit", "sues", "class action")),
    ("ev-itinerance", "event", ("itinérance", "itinérants", "sans-abri", "campement", "campements"), ("homelessness", "homeless", "encampment", "encampments")),
    ("ev-logement", "event", ("logement", "logements", "loyer", "loyers", "locataires"), ("housing", "rent", "rents", "tenants")),
    ("ev-immigration", "event", ("immigration", "immigrants", "seuils d'immigration", "demandeurs d'asile"), ("immigration", "immigrants", "asylum seekers")),
    ("ev-urgences", "event", ("urgences", "salle d'urgence"), ("emergency room",)),
    ("ev-eclosion", "event", ("éclosion", "éclosions"), ("outbreak", "outbreaks")),
    ("ev-vaccination", "event", ("vaccination", "vaccin", "vaccins"), ("vaccination", "vaccine", "vaccines")),
    ("ev-tremblement-de-terre", "event", ("tremblement de terre", "séisme"), ("earthquake",)),
    ("ev-glissement", "event", ("glissement de terrain",), ("landslide",)),
    ("ev-surverse", "event", ("surverse", "surverses", "eaux usées"), ("sewage overflow", "wastewater")),
    ("ev-mises-a-pied", "event", ("mises à pied", "licenciements", "fermeture d'usine"), ("layoffs", "laid off", "plant closure")),
    ("ev-investissement", "event", ("investissement", "investissements"), ("investment", "investments")),
    ("ev-subvention", "event", ("subvention", "financement", "aide financière"), ("grant", "funding", "financial aid")),
    ("ev-autobus", "event", ("autobus", "chauffeur d'autobus"), ("bus", "bus driver")),
    ("ev-cyberattaque", "event", ("cyberattaque", "piratage"), ("cyberattack", "hack", "data breach")),
    # ---- public roles (weakest evidence) -----------------------------------
    ("role-maire", "role", ("maire", "mairesse"), ("mayor",)),
    ("role-premier-ministre", "role", ("premier ministre", "première ministre"), ("premier", "prime minister")),
    ("role-ministre", "role", ("ministre",), ("minister",)),
    ("role-chef", "role", ("chef", "cheffe"), ("leader",)),
    ("role-depute", "role", ("député", "députée", "députés"), ("MNA", "MP", "MNAs", "MPs")),
    ("role-conseiller", "role", ("conseiller municipal", "conseillère municipale"), ("city councillor", "councillor")),
    ("role-juge", "role", ("juge",), ("judge",)),
    ("role-syndicat", "role", ("syndicat", "syndicats", "syndiqués"), ("labour union", "trade union", "unions")),
    ("role-opposition", "role", ("opposition",), ("opposition",)),
)

KIND_WEIGHT = {
    "place": 1.0,
    "road": 1.0,
    "project": 0.8,
    "institution": 0.6,
    "event": 0.5,
    "party": 0.35,
    "broad": 0.15,
    "role": 0.15,
}

# Kinds that pin a happening to a place; two items that each name one and
# share none are (weak) evidence of two different happenings.
SPECIFIC_PLACE_KINDS = frozenset({"place", "road"})


def fold(text: str) -> str:
    """Lowercase, strip accents, punctuation to spaces, single spaces."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _build():
    surface_to_id: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for eid, kind, fr, en in ENTRIES:
        meta[eid] = {"kind": kind, "label": fr[0], "label_en": en[0] if en else fr[0]}
        for surface in (*fr, *en):
            # Strip editorial parentheses ("Québec (ville)") from matchable text.
            key = fold(re.sub(r"\(.*?\)", " ", surface))
            if key and key not in surface_to_id:
                surface_to_id[key] = eid
    # Longest surface first so alternation prefers the longest match at a position.
    surfaces = sorted(surface_to_id, key=lambda s: (-len(s), s))
    pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(s) for s in surfaces) + r")(?![a-z0-9])")
    return surface_to_id, meta, pattern


SURFACE_TO_ID, META, _PATTERN = _build()


def find_terms(text: str, folded: bool = False) -> list[tuple[str, str]]:
    """Return (canonical id, matched folded surface) in reading order."""
    body = text if folded else fold(text)
    return [(SURFACE_TO_ID[m.group(0)], m.group(0)) for m in _PATTERN.finditer(body)]


def term_ids(text: str, folded: bool = False) -> set[str]:
    return {eid for eid, _ in find_terms(text, folded=folded)}


def kind_of(eid: str) -> str:
    return META.get(eid, {}).get("kind", "")


def label_of(eid: str) -> str:
    return META.get(eid, {}).get("label", eid)


# Folded word -> canonical (French) word, for bilingual token overlap. Every
# single-word surface of an entry (French or English) folds onto the entry's
# first single-word French surface, so "fire", "blaze" and "incendies" all
# read as "incendie"; then a hand list of common English headline nouns.
# Precision over recall: verbs and people-words mostly stay unmapped.
_EXTRA_WORDS = {
    "city": "ville", "council": "conseil", "government": "gouvernement",
    "mayor": "maire", "minister": "ministre", "premier": "premier",
    "school": "ecole", "schools": "ecole", "hospital": "hopital", "hospitals": "hopital",
    "street": "rue", "bridge": "pont", "highway": "autoroute", "road": "route",
    "building": "immeuble", "house": "maison", "home": "maison",
    "airport": "aeroport", "airports": "aeroport", "privatization": "privatisation",
    "child": "enfant", "children": "enfant", "kids": "enfant", "teen": "adolescent",
    "teenager": "adolescent", "woman": "femme", "women": "femme", "man": "homme", "men": "homme",
    "driver": "conducteur", "motorist": "automobiliste", "victim": "victime", "victims": "victime",
    "suspect": "suspect", "body": "corps", "car": "voiture", "truck": "camion",
    "project": "projet", "plan": "plan", "report": "rapport", "study": "etude",
    "price": "prix", "prices": "prix", "cost": "cout", "costs": "cout",
    "million": "million", "billion": "milliard", "year": "an", "years": "an",
    "week": "semaine", "night": "nuit", "morning": "matin", "evening": "soir",
    "tree": "arbre", "trees": "arbre", "park": "parc", "library": "bibliotheque",
    "university": "universite", "students": "etudiant", "student": "etudiant",
    "teachers": "enseignant", "teacher": "enseignant", "nurses": "infirmiere", "nurse": "infirmiere",
    "doctors": "medecin", "doctor": "medecin", "patients": "patient",
    "workers": "travailleur", "employees": "employe", "jobs": "emploi", "job": "emploi",
    "company": "entreprise", "plant": "usine", "factory": "usine",
    "water": "eau", "river": "riviere", "lake": "lac", "island": "ile",
    "budget": "budget", "deficit": "deficit", "debt": "dette",
    "three": "trois", "four": "quatre", "five": "cinq", "six": "six", "seven": "sept",
    "eight": "huit", "nine": "neuf", "ten": "dix",
    "first": "premier", "second": "deuxieme", "third": "troisieme",
}


def _word_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for _eid, _kind, fr, en in ENTRIES:
        fr_words = [fold(s) for s in fr if " " not in fold(s)]
        if not fr_words:
            continue
        target = fr_words[0]
        for s in (*fr, *en):
            w = fold(s)
            if w and " " not in w and w != target and w not in out:
                out[w] = target
    for k, v in _EXTRA_WORDS.items():
        out.setdefault(k, out.get(v, v))
    return out


WORD_CANON = _word_map()
