# Kniha jízd pro Home Assistant

Vlastní integrace sleduje jednotlivé úseky jízdy podle připojení Android Auto,
počká na opožděnou cloudovou aktualizaci tachometru, rozpozná známý cíl a umí
vytvořit dvoulistý Excel report.

## Instalace

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
- Známé místo v nastaveném poloměru se zařadí automaticky. Neznámý cíl vyvolá
  actionable notification se čtyřmi akcemi.

Aktivní jízda, čekající ukončení i nezodpovězená notifikace jsou uloženy v interním
HA Store. Restart Home Assistantu proto rozpracovanou jízdu nezahodí. Pokud začne
další jízda dříve, než cloud doplní předchozí tachometr, její počáteční stav se po
doručení předchozí finální hodnoty opraví na tuto hodnotu.

### Akce notifikace

- **Potvrdit klienta** – použije název odhadnutý mapou jako služební jízdu.
- **Navrhnout nového** – textový vstup uloží vlastní název jako služební jízdu.
- **Osobní KM** – označí segment jako soukromý.
- **Tankování** – textový vstup přijímá `litry; celková cena`, například
  `42,5; 1650`. Oba údaje jsou volitelné a jízda se počítá jako služební.

Volba se spolu se souřadnicemi a typem jízdy uloží do
`/config/learned_places.json`. Příští cíl v nastaveném poloměru se už neptá.

## Datové soubory

`/config/kniha_jizd_raw.json` má kořenový objekt s verzí formátu a polem
`segments`. Každý segment obsahuje stabilní ID, lokální datum, přesné UTC časy,
oba raw stavy tachometru, čas finální aktualizace, příznak timeoutu, celé adresy,
GPS, účel, typ jízdy, zdroj klasifikace, mapový odhad a případně litry/cenu.

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

## Nominatim a soukromí

Výchozí reverse geocoder je veřejný Nominatim. Integrace dodržuje maximálně jeden
požadavek za sekundu a známé cíle lokálně cacheuje. Nastavte identifikující
User-Agent, ideálně také kontaktní e-mail. Veřejnou službu nepoužívejte pro větší
firemní flotilu; v konfiguraci lze přepnout na vlastní či smluvní kompatibilní
endpoint. Aktuální podmínky jsou v
[Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/).

Souřadnice neznámého startu/cíle jsou při lookupu odeslány z HA na zvolený
geocoding endpoint. Do Excelu se ukládá atribuce OpenStreetMap, je-li lookup použit.
