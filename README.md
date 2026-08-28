# Kniha jízd pro Home Assistant

Vlastní integrace sleduje jednotlivé úseky jízdy podle připojení Android Auto,
počká na opožděnou cloudovou aktualizaci tachometru, rozpozná známý cíl a umí
vytvořit dvoulistý Excel report.

## Instalace

### HACS

1. V HACS otevřete **Integrace → Vlastní repozitáře**.
2. Přidejte `https://github.com/NioCZ/knihajizdHA` jako typ **Integrace**.
3. Stáhněte poslední vydanou verzi a restartujte Home Assistant.

Pro spolehlivé verzování používejte publikované GitHub Releases. Samotná větev
`main` je podporovaná také, HACS ji ale označuje zkráceným hashem commitu.

### Ruční instalace

1. Zkopírujte adresář `custom_components/kniha_jizd` do stejné cesty v `/config`.
2. Restartujte Home Assistant.
3. Otevřete **Nastavení → Zařízení a služby → Přidat integraci → Kniha jízd**.
4. Vyberte vstupní entity a službu telefonu. Službu lze zadat jako
   `mobile_app_telefon` i `notify.mobile_app_telefon`.

Výchozí nastavení počítá s těmito objekty:

- `binary_sensor.android_auto`
- `device_tracker.telefon`
- `sensor.telefon_geocoded_location`
- `sensor.skoda_odometer`
- `notify.mobile_app_telefon`

Adresy a souřadnice domova a firmy nejsou ve veřejném kódu předvyplněné.
Vlastní hodnoty lze bezpečně zadat pouze do lokální konfigurace přes
**Konfigurovat**; název firmy má neutrální výchozí štítek **Firma**.

Nastavení lze později změnit přes tlačítko **Konfigurovat** u integrace; změna
integraci bezpečně reloaduje.

## Stavové entity a vlastní stránka

Po nastavení vznikne zařízení **Kniha jízd** s průběžně aktualizovanými entitami:

- `sensor.kniha_jizd_stav` – `idle`, `driving`, `waiting_odometer`,
  `waiting_classification` nebo `error`; v atributech jsou kontroly všech vstupů,
- `binary_sensor.kniha_jizd_pripravena` – zapnuto, když funguje Android Auto,
  GPS, tachometr a notifikační služba,
- senzory dnešních služebních/soukromých km a počtu dnešních jízd,
- počet čekajících, celkový počet jízd a celkové služební/soukromé kilometry,
- poslední jízda včetně zákazníka, trasy, času a validačního výsledku,
- stav posledního Excel exportu a jeho dočasný odkaz ke stažení,
- `button.kniha_jizd_vygenerovat_excel` pro export z dashboardu či automatizace.

Přesné `entity_id` může Home Assistant doplnit příponou, pokud už stejné ID
existuje. Všechny entity jsou seskupené pod jedním zařízením.

Integrace zároveň registruje administrační stránku **Kniha jízd** v levém panelu
Home Assistantu. Ukazuje aktuální průběh, zdraví jednotlivých vstupů, dnešní
součty, poslední jízdu, editovatelnou tabulku dnešních jízd, výběr měsíce a
tlačítko **Vygenerovat a stáhnout Excel**. Stránka je dostupná pouze
administrátorům.

Kontrola GPS přijímá standardní atributy `latitude`/`longitude` vybrané GPS
entity. Pokud je novější model `device_tracker` neposkytuje, automaticky použije
atribut `location` ze senzoru geokódované adresy Companion aplikace. Tachometr
umí kromě čistého číselného stavu přečíst také hodnotu s jednotkou nebo oddělovači
(například `98 332 km`) a běžné atributy `odometer`, `mileage` či
`total_distance`. Panel u obou vstupů zobrazuje skutečně použitý zdroj hodnoty.

## Jak probíhá jízda

- Přechod Android Auto `off → on` uloží čas, stav tachometru a výchozí polohu.
- Přechod `on → off` okamžitě zachytí záložní cílovou polohu, požádá Companion
  aplikaci o aktualizaci GPS a výchozích 60 sekund čeká na její ustálení. Potom
  spustí mapové rozpoznání a actionable notification. Tachometr se po celou dobu
  zpracovává souběžně.
- Primární signál finálního tachometru vyžaduje čas `last_updated` (nebo HA
  metadata `State.last_updated`) **po** odpojení a současně vyšší stav počitadla
  než na začátku segmentu. U chybějícího počátečního stavu stačí nový čas a
  platná hodnota.
- Pokud primární signál ještě nepřišel, segment zůstane ve stavu čekání na
  tachometr bez časového limitu. Po restartu Home Assistantu se čekání obnoví a
  jízda se zapíše až po důvěryhodné novější hodnotě; zastaralý stav ani `0 km`
  se jako náhradní výsledek nepoužije.
- Potvrzené služební místo ve výchozím okruhu 500 m se zařadí automaticky.
  Soukromé místo používá výchozí okruh 250 m. U neznámého cíle se
  v okruhu 3 000 m vyhledají a obodují odborné instituce a odešle se notifikace.

Na notifikaci lze odpovědět ještě před dokončením tachometru. Klasifikace se uloží
do HA Store a segment se automaticky zapíše, jakmile získá finální kilometry. Po
úspěšné volbě integrace odešle pro stejný `tag` příkaz `clear_notification`, takže
původní otázka z telefonu zmizí.

Pokud další jízda začne během čekání na polohu, její start se použije jako přesný
cíl předchozího úseku. Také později se blízký start srovná s předchozím cílem,
aby řetězec neměl dvě odlišné adresy pro stejné parkování. Při restartu dlouho po
ukončení se použije původní záložní poloha místo aktuální polohy telefonu.

### Nastavený domov a firma

Je-li dostupná GPS, integrace porovná cíl se zadanými souřadnicemi a použije
samostatný poloměr domova nebo firmy (výchozí 300 m). Text telefonu pak nemůže
přebít GPS mimo tento okruh. Jen při chybějící GPS se krátká nastavená adresa
porovnává s celou geokódovanou adresou: musí souhlasit ulice, číslo domu a
všechny další zadané části. PSČ, stát, interpunkce ani diakritika shodu
neovlivní. Použitá metoda a vzdálenost se ukládají do
`configured_place_match` v raw datech.

Při sestavení souhrnného Excelu se každý bod uvnitř nastaveného okruhu domova
nebo firmy zapíše přesnou adresou z konfigurace. Nepřesná mobilní adresa (např.
vedlejší číslo domu) zůstává jen v detailu jízd a v listu **Raw data** pro audit.

- Nastavená adresa domova se rozpozná jako **Domov**. Při platné návaznosti na
  předchozího zákazníka se automaticky použije služební návrat; bez návaznosti se
  integrace zeptá, protože cesta domů může být také soukromá.
- Nastavená adresa firmy se automaticky uloží jako služební cíl pod zadaným
  názvem firmy.

### Služební návraty domů, na firmu nebo do hotelu

Integrace nehádá typ jízdy jen podle cíle, protože stejná cesta domů může být
služební i soukromá. Místo toho kontroluje návaznost na poslední uložený segment:

- předchozí segment musí být služební,
- nová jízda musí začít v nastaveném okruhu od jeho cíle (případně na přesně
  stejné adrese, pokud GPS chybí),
- časová mezera nesmí překročit nastavenou hodnotu, výchozí je 18 hodin,
- pokud jsou známé oba stavy tachometru, jejich rozdíl nesmí být větší než 1 km;
  větší rozdíl znamená, že mezi segmenty pravděpodobně proběhla jiná cesta.

Při první takové jízdě nabídne notifikace **Služební návrat**, **Jiný klient** a
**Osobní KM**. Potvrzený domov, firma či hotel se uloží s rolí `return`. Příští
návrat na stejné místo se zapíše automaticky jako služební jen při platné
návaznosti na klienta; bez návaznosti se integrace znovu zeptá. Služební návrat
si ponechá zákazníka předchozího segmentu a v raw datech má `journey_role: return`
a `return_of_segment_id`, takže agregovaný Excel zákazníka nerozdělí na dvě jména.

Maximální čas návaznosti lze změnit přes **Konfigurovat** u integrace v rozsahu
1–72 hodin. Volnější hodnota je vhodná pro přenocování v hotelu, kratší omezuje
riziko, že se za návrat nabídne pozdější nesouvisející cesta.

### Celá jízda a krátké mezizastávky

Odpojení Android Auto na benzince, odpočívadle, při nabíjení,
občerstvení nebo rychlém nákupu samo o sobě neurčuje účel jízdy. Nominatim vrací
vedle názvu také OSM kategorii a typ místa. Integrace z nich vytvoří dočasnou
mezizastávku a čeká na pokračování celé cesty.

Segmenty se spojí do jednoho `journey_id`, pouze když:

- další jízda začne nejpozději do 60 minut (nastavitelné 5–180 minut),
- začne nejvýše 500 m od místa zastavení; pokud je užší poloměr potvrzených míst,
  použije se tato užší hodnota,
- známý konečný a nový počáteční stav tachometru se neliší o více než 1 km.

Velmi krátké stání do 3 minut se při odjezdu ze stejného místa považuje za
mezizastávku i bez spolehlivé mapové kategorie. Tím se například rychlé zastavení
na benzince nebo krátké vysazení nepropíše jako samostatný bod denní trasy.

Skutečný cíl následně zařadí celý řetězec. Například `firma → benzinka → klient`
převezme zákazníka i služební typ z klienta; `domov → obchod → domov` převezme
soukromý typ z domova. Totéž funguje při návratu `klient → odpočívadlo → hotel`.
Mezizastávky se v agregovaném Excelu ve sloupci **Přes** nezobrazují, ale jejich
kilometry se stále započítají ke konečnému účelu celé jízdy.

Obyčejné parkoviště se samo o sobě nepovažuje za tranzitní zastávku, aby nezmizela
skutečná návštěva zákazníka, který ještě není dobře zakreslený v OpenStreetMap.
Výjimkou je parkoviště pojmenované jako odpočívadlo či servisní místo. Potvrzená
tranzitní místa se učí jen s malým poloměrem 200 m.
Pokud po předpokládané mezizastávce v časovém limitu žádná jízda nezačne,
integrace pošle běžnou otázku a segment lze zařadit samostatně.

V raw datech jsou pro audit dostupná pole `journey_id`,
`journey_role: transient_stop`, `transient_stop`, `continuation` a
`journey_inherited_from_segment_id`. Pole `journey_segment_count` a
`journey_distance_km` obsahují počet segmentů a celkovou délku analyzované cesty.
Rozpracovaný řetězec je uložen v HA Store a přežije restart Home Assistantu.

Aktivní jízda, čekající ukončení i nezodpovězená notifikace jsou uloženy v interním
HA Store. Restart Home Assistantu proto rozpracovanou jízdu nezahodí. Pokud začne
další jízda dříve, než cloud doplní předchozí tachometr, začátek nové jízdy vytvoří
pevnou časovou hranici. Předchozí úsek se dočasně uzavře podle GPS vzdálenosti a
už nesmí spotřebovat pozdější společnou aktualizaci tachometru. Jakmile cloud
dodá další důvěryhodný stav, celkový přírůstek se zpětně rozdělí mezi všechny
dotčené úseky. Nejde o časový timeout: čekání končí až skutečným začátkem další
jízdy. Denní kontrola stejným pravidlem rozpozná i chybnou pozdní hranici
uloženou starší verzí a nahradí ji GPS odhadem, dokud není k dispozici další
společný stav tachometru.

### Zpětná kontrola kilometrů

Cloud může jednu aktualizaci tachometru doručit až po zahájení několika dalších
úseků. Taková hodnota se už nepřiřadí celá nejstarší čekající jízdě. Segmenty mezi
posledním a následujícím důvěryhodným stavem odometru se zpětně vyhodnotí jako
skupina. Celkový přírůstek se rozdělí podle poměru jejich GPS vzdáleností a součet
musí odpovídat rozdílu obou stavů tachometru. Čerstvý stav při příštím odjezdu
může předchozí úsek uzavřít přesně ještě před další jízdou.

Panel ukazuje **Denní kontrolu km**: přírůstek odometru, součet přiřazených
segmentů, rozdíl a počet dosud neuzavřených úseků. Raw data zachovávají původní
`distance_km_raw` i způsob výsledku v `distance_reconciliation_source`. Ručně
opravené km mají `manual_distance_override: true` a automatika je nepřepíše.
Panel proto ruční přepis nastaví pouze tehdy, když uživatel pole kilometrů
skutečně změní; pouhá změna typu nebo volitelného zákazníka automatické km
nezamkne.

Pokud některá dřívější cloudová hodnota odporuje novějšímu koncovému stavu
(například segmenty dávají 163 km, ale rozdíl prvního a posledního odometru je
156 km), přednost má nejnovější denní stav. Integrace přepočítá všechny dotčené
úseky tak, aby daly přesně 156 km. Kilometry se ukládají a zobrazují jako celá
čísla. Používá se rozdělení metodou největších zbytků, takže zaokrouhlení nikdy
nezmění denní součet; skutečně ujeté úseky dostanou minimálně 1 km, pokud je
celkový nájezd alespoň tak velký jako počet segmentů. Původní desetinný výpočet
zůstává v `distance_km_raw`.

### Akce notifikace

- **Potvrdit klienta** – použije nejpravděpodobnější mapový návrh.
- **Navrhnout nového** – přijme vlastní název nebo číslo návrhu `1`, `2` či `3`.
- **Osobní KM** – označí segment jako soukromý.

U rozpoznané návazné jízdy se první dvě volby nahradí tlačítky **Služební návrat**
a **Jiný klient**. Návrat je uložen jen jako vztah mezi jízdami (`journey_role` a
`return_of_segment_id`); nevytváří ani neučí samostatný typ místa.

Volba se spolu se souřadnicemi a typem jízdy uloží do
`/config/learned_places.json`. Příští cíl v nastaveném poloměru se už neptá.
Soukromé cíle se učí pod názvem mapového místa s nastavitelnou výchozí zónou 250 m,
takže několik různých soukromých cílů nesplyne do jedné široké kilometrové zóny.
V raw jízdě zůstává účel `Soukromá`.

Každý fyzický parkovací bod má jediný záznam. Běžné známé místo nese právě jednu
výchozí klasifikaci (`business` nebo `private`) a další návštěva se zařadí
automaticky. Pokud je stejné místo vědomě potvrzené oběma způsoby, uloží se jako
jedna výjimka s oběma hodnotami a telefon se při další návštěvě zeptá pouze na typ
jízdy; druhý bod na mapě nevznikne.

### Denní tabulka a dodatečné opravy

Panel **Kniha jízd** zobrazuje všechny dnešní uložené i rozpracované segmenty.
U každého ukazuje adresy, kilometry, zákazníka, typ a stav zpracování. Pole
**Odkud**, **Kam**, **km**, **Zákazník / účel** a **Typ** lze upravit tlačítkem
**Uložit**. Zákazník je u služební cesty volitelný; prázdná hodnota se v
souhrnném Excelu nezobrazí. Příchozí aktualizace HA během psaní ani otevřený
výběr typu už tabulku nepřekreslí. Rozpracované hodnoty zůstanou zachované i po
opuštění pole, otevřené vysvětlení rozhodnutí se samo nezavře a po uložení se
zobrazí opravená hodnota místo krátkého návratu ke starému stavu.

To funguje také v případě, kdy byla mobilní notifikace omylem smazána: segment
zůstane ve stavu **Čeká na zařazení** a lze jej dokončit přímo v tabulce. Pokud
ještě čeká tachometr, ruční volba se uloží a finální zápis proběhne později.
Oprava již uložené cesty změní všechny segmenty se stejným `journey_id`, takže
benzinka či odpočívadlo nezůstanou v jiném typu než konečný cíl. Současně se
přeučí výchozí typ odpovídajícího fyzického místa; další návštěva proto neopakuje
původní chybnou klasifikaci.

Stejnou opravu lze volat jako HA akci:

```yaml
action: kniha_jizd.update_trip
data:
  segment_id: "ID_Z_TABULKY_NEBO_RAW_DAT"
  purpose: "Genetická laboratoř"
  trip_type: business
```

### Mapa míst a zón

Záložka **Mapa míst** v administračním panelu načítá přes přihlášené HA API:

- aktuální GPS auta, použitý zdroj polohy a informaci, zda je auto uvnitř známé zóny,
- nakonfigurovaný domov a firmu,
- všechny fyzické body z `learned_places.json` včetně názvu, role, adresy a skutečně
  použitého rozpoznávacího poloměru,
- dnešní uložené i rozpracované úseky jízdy.

Mapa rozlišuje klienty a soukromá místa. Nakonfigurovaný domov a firma
mají vždy jen jeden bod bez ohledu na to, zda tam vedla soukromá nebo služební
jízda. Krátké zastávky, například benzinky a obchody, se na mapě nekreslí a jejich
úseky se sloučí do celé cesty k výslednému cíli. Interně zůstávají zapamatované jen
pro správné rozpoznání návaznosti jízd. Totéž platí pro interní návratový kontext:
ovlivní zařazení trasy, ale nevytváří kategorii místa na mapě. Podklad tvoří
dlaždice OpenStreetMap. Po výběru naučeného bodu lze přímo v jeho detailu použít
**Odstranit označený bod**. Smaže se jen vybraný fyzický GPS bod; historické jízdy
zůstanou zachované. Konfigurovaný domov a firmu mapa odstranit nedovolí, protože
se upravují v nastavení integrace.

Krátká zastávka nevyvolá okamžitou notifikaci. Integrace čeká nastavený počet
minut na pokračování: pokud další jízda začne, zastávka zdědí klasifikaci celé
cesty. Pokud nepokračuje, přijde běžná otázka a potvrzený obchod či jiné místo se
uloží jako standardní soukromý nebo služební cíl. Každé známé soukromé místo po
služebním cíli nejdřív počká na možné pokračování: při odjezdu v limitu zdědí
služební cestu, bez pokračování se samo uloží jako soukromý cíl. Známé služební
místo má naopak přednost a zaznamená se jako skutečný cíl i při krátké návštěvě.
Pokud otázka na samostatnou krátkou zastávku zůstane bez odpovědi, po výchozích
24 hodinách se segment uloží jako **Nevyřešený – k revizi**. Jeho kilometry se do
opravy nezapočtou ani jako služební, ani jako soukromé a segment už nezůstává
neomezeně v runtime frontě.

### Správa míst

Záložka **Správa míst** zobrazuje každý fyzický bod jako samostatný řádek, jeho
typ a skutečný poloměr. Bod lze přejmenovat, přepnout mezi služebním,
soukromým, smíšenou výjimkou a interní krátkou zastávkou, změnit mu poloměr nebo
jej odstranit. Shodný název vzdálené body nikdy nespojí. Automaticky se sloučí jen
GPS duplicity vzdálené nejvýše 25 m; stejné omezení chrání i ruční sloučení.
Historické jízdy zůstávají při odstranění bodu zachované.

### Historie a kalendář

Záložka **Historie** umožňuje přepínat měsíce a vybrat libovolný den. Kalendář
zobrazuje u každého dne modře služební, fialově soukromé kilometry a oranžově počet
jízd čekajících na revizi. Po kliknutí
na den se zobrazí jeho souhrn a stejná editovatelná tabulka jízd jako v dnešním
přehledu. Historická data poskytuje pouze přihlášené administrační API a oprava
řádku se promítne do denních i měsíčních součtů. Sloupec **Rozhodnutí** rozbalí
zdroj klasifikace, nalezené místo, vzdálenost a poloměr, návratový kontext i stav
vyhledávání okolních institucí. Při rychlém přepínání dnů nebo měsíců se použije
poslední volba; opožděná starší odpověď už tabulku nevrátí zpět.

## Rozpoznání nemocnic a výzkumných pracovišť

Rozpoznávání používá oddělené vzdálenosti:

- **Domov** – výchozí 300 m.
- **Firma** – výchozí 300 m.
- **Služební místo** – výchozí 500 m. Vztahuje se na klienty,
  které už uživatel potvrdil. Nejbližší shoda se zapíše automaticky. Je-li GPS
  dostupná, shodná textová adresa nemůže tento okruh obejít; adresa je záloha jen
  při chybějících souřadnicích.
- **Soukromé místo** – výchozí 250 m.
- **Návaznost krátké zastávky** – výchozí 200 m.
- **Poloměr hledání nových institucí** – výchozí 3 000 m. Slouží jen pro sestavení
  návrhů; samotný mapový odhad se bez potvrzení nezapíše.

Jeden Overpass dotaz načte objekty označené jako nemocnice, klinika, krevní banka,
odběrové/transfuzní centrum, univerzita, výzkumný ústav, výzkumná kancelář,
laboratoř nebo univerzitní pracoviště. Kandidáti
získávají body za odpovídající OSM kategorii a za výskyty nakonfigurovaných kořenů
slov. Výchozí sada zvýhodňuje genetiku, genomiku, DNA, molekulární a biomedicínská
pracoviště, laboratoře, cytogenetiku, sekvenování, patologii, onkologii a
mikrobiologii. Za vzdálenost se body odečítají. Proto může relevantní genetický
ústav porazit bližší obecnou nemocnici nebo univerzitu.

Každý dokončený pokus ukládá také čas, použité souřadnice, počet výsledků, stav,
počet pokusů, použití cache a případnou chybu do raw dat. Overpass dotaz se při
dočasné chybě opakuje až třikrát; úspěšné i prázdné výsledky se šest hodin cacheují
a při výpadku lze použít i starší cache. Pokud mobilní notifikační služba při
dojezdu ještě není zaregistrovaná,
čekající otázka se automaticky odešle po jejím zpřístupnění.

Do notifikace se vloží až tři nejlepší výsledky se vzdáleností. Tlačítko potvrzení
vybere první; v textovém vstupu lze napsat číslo druhého/třetího výsledku nebo úplně
vlastní název. Kompletní skóre, důvody a kandidáti zůstávají v raw datech pro audit.

Každý záznam v `learned_places.json` představuje právě jeden fyzický bod. Stejný
název může mít více samostatných bodů (například různé pobočky), ale každý má své
ID, klasifikaci a poloměr. Při aktualizaci se staré vícekotvové záznamy automaticky
rozdělí; pouze body do 25 m se ponechají jako jedna GPS duplicita. Starý formát
s jednou dvojicí latitude/longitude se načítá zpětně kompatibilně. Volitelným
ručním polem `radius_m` lze konkrétnímu bodu přepsat globální poloměr.

## Datové soubory

`/config/kniha_jizd_raw.json` má kořenový objekt s verzí formátu a polem
`segments`. Každý segment obsahuje stabilní ID, lokální datum, přesné UTC časy,
oba raw stavy tachometru, čas finální aktualizace, kompatibilní příznak timeoutu
(u nových jízd vždy `false`), celé adresy, GPS, účel, typ jízdy, zdroj
klasifikace a mapový odhad.
Pro české cíle se v běžných sloupcích odstraní PSČ, stát a administrativní kraj;
plná původní hodnota zůstává v `start_address_raw` a `end_address_raw`. Zahraniční
adresa se nezkracuje.
U nových míst navíc obsahuje seřazené `map_candidates`, použitý vyhledávací okruh
a případně vybraného mapového kandidáta.

Zápisy obou JSON souborů probíhají atomickou výměnou souboru. ID segmentu navíc
brání duplicitnímu zápisu při opakování akce nebo zotavení po restartu.

## Excel export

Zavolejte akci:

```yaml
action: kniha_jizd.export_excel
data:
  month: "2026-08"
  path: kniha_jizd.xlsx
```

Cesta musí končit `.xlsx` a z bezpečnostních důvodů musí zůstat uvnitř `/config`.
Výchozí soubor je `/config/kniha_jizd.xlsx`. Samotný pandas/openpyxl export
běží přes `hass.async_add_executor_job`, takže neblokuje event loop.

Parametr `month` používá formát `YYYY-MM`; pokud se neuvede, exportuje se aktuální
měsíc podle časové zóny Home Assistantu. Filtr se vztahuje na souhrnný i raw list.

Po exportu vznikne náhodný odkaz platný 15 minut. Díky tomu report s adresami
nemusí ležet ve veřejně dostupném `/config/www`. Nový export starý odkaz zneplatní.

- **Kniha jízd**: jeden řádek na den vybraného měsíce, trasa `Start/Odkud → Přes → Cíl/Kam`,
  unikátní zákazníci a součty služebních/soukromých kilometrů. Soukromé segmenty
  přispívají pouze do **Soukromé km**; jejich adresy ani interní účel se do
  souhrnné trasy a zákazníků nevkládají. U čistě soukromého dne proto zůstávají
  sloupce Odkud/Přes/Kam/Zákazník prázdné. Rozpoznaný domov a firma se zde vždy
  zobrazí přesnou adresou z konfigurace, ne sousední adresou určenou mobilem.
- **Raw data**: všechny segmenty vybraného měsíce ve stejných polích jako JSON log.
  Zde zůstávají původní adresy i u soukromých jízd pro případný audit a opravu.

## Mapové služby a soukromí

Výchozí reverse geocoder je veřejný Nominatim. Integrace dodržuje maximálně jeden
požadavek za sekundu a známé cíle lokálně cacheuje. Nastavte identifikující
User-Agent, ideálně také kontaktní e-mail. Veřejnou službu nepoužívejte pro větší
firemní flotilu; v konfiguraci lze přepnout na vlastní či smluvní kompatibilní
endpoint. Aktuální podmínky jsou v
[Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/).

Kandidáty odborných institucí dodává samostatný Overpass endpoint. Používá se jeden
sloučený dotaz na neznámý cíl, nejvýše jeden současně a s odstupem nejméně dvě
sekundy mezi jednotlivými pokusy. Oba endpointy jsou nastavitelné, takže je lze
nahradit vlastními službami.

Souřadnice neznámého startu/cíle jsou při lookupu odeslány z HA na zvolený
Nominatim endpoint; souřadnice neznámého cíle také na zvolený Overpass endpoint.
Do Excelu se ukládá atribuce OpenStreetMap, je-li mapové hledání použito.
