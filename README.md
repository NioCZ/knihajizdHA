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

## Jak probíhá jízda

- Přechod Android Auto `off → on` uloží čas, stav tachometru a výchozí polohu.
- Přechod `on → off` okamžitě zachytí cílovou polohu. Mapové rozpoznání a
  actionable notification se spustí ihned, souběžně s tachometrem.
- Primární signál finálního tachometru vyžaduje čas `last_updated` (nebo HA
  metadata `State.last_updated`) **po** odpojení a současně vyšší stav počitadla
  než na začátku segmentu. U chybějícího počátečního stavu stačí nový čas a
  platná hodnota.
- Pokud oba primární signály nepřijdou, použije se po timeoutu (výchozí 600 s)
  poslední dostupná hodnota. Raw data pak obsahují
  `odometer_wait_timed_out: true` a
  `odometer_completion_source: timeout_latest_value`.
- Potvrzené místo v okruhu 1 000 m se zařadí automaticky. U neznámého cíle se
  v okruhu 3 000 m vyhledají a obodují odborné instituce a odešle se notifikace.

Na notifikaci lze odpovědět ještě před dokončením tachometru. Klasifikace se uloží
do HA Store a segment se automaticky zapíše, jakmile získá finální kilometry.

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

Skutečný cíl následně zařadí celý řetězec. Například `firma → benzinka → klient`
převezme zákazníka i služební typ z klienta; `domov → obchod → domov` převezme
soukromý typ z domova. Totéž funguje při návratu `klient → odpočívadlo → hotel`.
Mezizastávky se v agregovaném Excelu zachovají ve sloupci **Přes**, ale jejich
kilometry se započítají ke konečnému účelu.

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
další jízda dříve, než cloud doplní předchozí tachometr, její počáteční stav se po
doručení předchozí finální hodnoty opraví na tuto hodnotu.

### Akce notifikace

- **Potvrdit klienta** – použije nejpravděpodobnější mapový návrh.
- **Navrhnout nového** – přijme vlastní název nebo číslo návrhu `1`, `2` či `3`.
- **Osobní KM** – označí segment jako soukromý.

U rozpoznané návazné jízdy se první dvě volby nahradí tlačítky **Služební návrat**
a **Jiný klient**. Návratové místo se učí kontextově, nikoli napevno jako služební.

Volba se spolu se souřadnicemi a typem jízdy uloží do
`/config/learned_places.json`. Příští cíl v nastaveném poloměru se už neptá.

### Denní tabulka a dodatečné opravy

Panel **Kniha jízd** zobrazuje všechny dnešní uložené i rozpracované segmenty.
U každého ukazuje adresy, kilometry, zákazníka, typ a stav zpracování. Pole
**Zákazník / účel** a **Typ** lze upravit tlačítkem **Uložit**.

To funguje také v případě, kdy byla mobilní notifikace omylem smazána: segment
zůstane ve stavu **Čeká na zařazení** a lze jej dokončit přímo v tabulce. Pokud
ještě čeká tachometr, ruční volba se uloží a finální zápis proběhne později.
Oprava již uložené cesty změní všechny segmenty se stejným `journey_id`, takže
benzinka či odpočívadlo nezůstanou v jiném typu než konečný cíl.

Stejnou opravu lze volat jako HA akci:

```yaml
action: kniha_jizd.update_trip
data:
  segment_id: "ID_Z_TABULKY_NEBO_RAW_DAT"
  purpose: "Genetická laboratoř"
  trip_type: business
```

## Rozpoznání nemocnic a výzkumných pracovišť

Vyhledávání má dvě nezávislé vzdálenosti:

- **Poloměr potvrzeného místa** – výchozí 1 000 m. Vztahuje se pouze na zákazníky,
  které už uživatel potvrdil. Nejbližší shoda se zapíše automaticky. Je-li GPS
  dostupná, shodná textová adresa nemůže tento okruh obejít; adresa je záloha jen
  při chybějících souřadnicích.
- **Poloměr hledání nových institucí** – výchozí 3 000 m. Slouží jen pro sestavení
  návrhů; samotný mapový odhad se bez potvrzení nezapíše.

Jeden Overpass dotaz načte objekty označené jako nemocnice, klinika, univerzita,
výzkumný ústav, výzkumná kancelář, laboratoř nebo univerzitní pracoviště. Kandidáti
získávají body za odpovídající OSM kategorii a za výskyty nakonfigurovaných kořenů
slov. Výchozí sada zvýhodňuje genetiku, genomiku, DNA, molekulární a biomedicínská
pracoviště, laboratoře, cytogenetiku, sekvenování, patologii, onkologii a
mikrobiologii. Za vzdálenost se body odečítají. Proto může relevantní genetický
ústav porazit bližší obecnou nemocnici nebo univerzitu.

Do notifikace se vloží až tři nejlepší výsledky se vzdáleností. Tlačítko potvrzení
vybere první; v textovém vstupu lze napsat číslo druhého/třetího výsledku nebo úplně
vlastní název. Kompletní skóre, důvody a kandidáti zůstávají v raw datech pro audit.

Jeden zákazník může mít v `learned_places.json` více potvrzených parkovacích bodů
(`anchors`). Když je stejný název potvrzen na vzdálenějším parkovišti, nový bod se
přidá ke stejnému zákazníkovi. Starý formát s jednou dvojicí latitude/longitude se
načítá zpětně kompatibilně. Volitelným ručním polem `radius_m` lze konkrétnímu
zákazníkovi přepsat globální poloměr.

## Datové soubory

`/config/kniha_jizd_raw.json` má kořenový objekt s verzí formátu a polem
`segments`. Každý segment obsahuje stabilní ID, lokální datum, přesné UTC časy,
oba raw stavy tachometru, čas finální aktualizace, příznak timeoutu, celé adresy,
GPS, účel, typ jízdy, zdroj klasifikace a mapový odhad.
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
  unikátní zákazníci a součty služebních/soukromých kilometrů.
- **Raw data**: všechny segmenty vybraného měsíce ve stejných polích jako JSON log.

## Mapové služby a soukromí

Výchozí reverse geocoder je veřejný Nominatim. Integrace dodržuje maximálně jeden
požadavek za sekundu a známé cíle lokálně cacheuje. Nastavte identifikující
User-Agent, ideálně také kontaktní e-mail. Veřejnou službu nepoužívejte pro větší
firemní flotilu; v konfiguraci lze přepnout na vlastní či smluvní kompatibilní
endpoint. Aktuální podmínky jsou v
[Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/).

Kandidáty odborných institucí dodává samostatný Overpass endpoint. Používá se jeden
sloučený dotaz na neznámý cíl, nejvýše jeden současně a s odstupem nejméně dvě
sekundy. Oba endpointy jsou nastavitelné, takže je lze nahradit vlastními službami.

Souřadnice neznámého startu/cíle jsou při lookupu odeslány z HA na zvolený
Nominatim endpoint; souřadnice neznámého cíle také na zvolený Overpass endpoint.
Do Excelu se ukládá atribuce OpenStreetMap, je-li mapové hledání použito.
