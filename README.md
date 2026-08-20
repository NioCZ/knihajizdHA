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

## Jak probíhá jízda

- Přechod Android Auto `off → on` uloží čas, stav tachometru a výchozí polohu.
- Přechod `on → off` okamžitě zachytí cílovou polohu, ale zápis zatím neuzavře.
- Integrace poslouchá změny tachometru a přijme až stav, jehož explicitní atribut
  `last_updated` (nebo HA metadata `State.last_updated`) je **po** čase odpojení.
- Po timeoutu (výchozí 600 s) použije poslední dostupnou hodnotu a v raw datech
  nastaví `odometer_wait_timed_out: true`.
- Potvrzené místo v okruhu 1 000 m se zařadí automaticky. U neznámého cíle se
  v okruhu 3 000 m vyhledají a obodují odborné instituce a odešle se notifikace.

Aktivní jízda, čekající ukončení i nezodpovězená notifikace jsou uloženy v interním
HA Store. Restart Home Assistantu proto rozpracovanou jízdu nezahodí. Pokud začne
další jízda dříve, než cloud doplní předchozí tachometr, její počáteční stav se po
doručení předchozí finální hodnoty opraví na tuto hodnotu.

### Akce notifikace

- **Potvrdit klienta** – použije nejpravděpodobnější mapový návrh.
- **Navrhnout nového** – přijme vlastní název nebo číslo návrhu `1`, `2` či `3`.
- **Osobní KM** – označí segment jako soukromý.

Volba se spolu se souřadnicemi a typem jízdy uloží do
`/config/learned_places.json`. Příští cíl v nastaveném poloměru se už neptá.

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
  path: www/kniha_jizd.xlsx
```

Cesta musí končit `.xlsx` a z bezpečnostních důvodů musí zůstat uvnitř `/config`.
Výchozí soubor je `/config/www/kniha_jizd.xlsx`. Samotný pandas/openpyxl export
běží přes `hass.async_add_executor_job`, takže neblokuje event loop.

- **Kniha jízd**: jeden řádek na den, trasa `Start/Odkud → Přes → Cíl/Kam`,
  unikátní zákazníci a součty služebních/soukromých kilometrů.
- **Raw data**: všechny segmenty ve stejných polích jako JSON log.

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
