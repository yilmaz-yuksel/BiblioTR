# BiblioTR

Türkiye'nin ulusal atıf dizini TR Dizin'den kayıt çekme, denetleme ve bibliyometrik analiz için dışa aktarma aracı.

[English](README.md) | **Türkçe**

[TR Dizin](https://search.trdizin.gov.tr) arama API'sinden anahtar kelimeye ve filtrelere göre yayın
verisi toplayan, SQLite'ta saklayan, bibliyometrik analizler yapan ve verileri VOSviewer,
Bibliometrix/Biblioshiny, Zotero gibi araçlara aktaran bir Python aracı ve web arayüzü.

API dokümanı: <https://development.trdizin.gov.tr/>

## Kurulum

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Web arayüzü

```bash
streamlit run app.py
```

Tarayıcıda `http://localhost:8501` açılır. Sekmeler:

| Sekme | İçerik |
|---|---|
| Veri Topla | Sorgu, tüm facet filtreleri, yıl aralığı, önizleme, tümünü çekip veri seti olarak kaydetme |
| Genel Bakış | Özet göstergeler, yıllık yayın ve atıf, dil / yayın türü / veritabanı dağılımı, en çok atıf alanlar |
| Yazar / Dergi / Kurum | Yazar metrikleri (yayın, kesirli sayım, ilk yazarlık, h ve g indeksi), en üretken dergi, kurum, kurum tipi, şehir, anahtar kelime |
| Kaynakça ve Atıf | TR Dizin / CrossRef / OpenAlex / OpenCitations atıf karşılaştırması, en sık atıf yapılan kaynaklar, kaynak yaşı ve Price indeksi |
| Yasalar | Lotka (yazar üretkenliği) ve Bradford (dergi bölgeleri) |
| Ağlar | Ortak yazarlık, kurum / şehir / ülke işbirliği, anahtar kelime ve konu eş-oluşum, ortak atıf, bibliyografik eşleşme, veri seti içi doğrudan atıf ağları; kümeler, merkezilik; VOSviewer dosyası |
| Tematik Evrim | Dönemlere göre anahtar kelime / konu ısı haritası |
| Eşanlamlılar | Yazar, kurum ve anahtar kelime birleştirme önerileri, onay / ayrı tut, sözlük yönetimi, VOSviewer ve bibliometrix sözlük dosyaları |
| Dışa Aktar | CSV, Excel, WoS düz metin, RIS, BibTeX |
| Ham Veri | Ham JSON alanları ile normalize edilmiş alanların karşılaştırması |

## Komut satırı

```bash
# Tek sayfa arama
bibliotr search "bibliometri" --limit 10 --facets

# Veri çekmeden tüm sonuç kümesinin profili (yıl, dergi, kurum, şehir, konu, yazar dağılımları)
# q isteğe bağlıdır; yalnızca konu ile de aranabilir
bibliotr profile --facet "subject=Fen > Tıp > Spor Bilimleri" --facet documentType=PAPER --out spor_profil.xlsx

# Dergi bilgisi (dizinlendiği ve reddedildiği yıllar, konular, editör, eksik sayılar)
bibliotr journal 488 --profile
# Bir derginin tüm yayınlarını çek (varsayılan: yalnızca TR Dizin'de dizinlendiği yıllar)
bibliotr harvest --journal 488 --dataset sbd

# Konu listesi (TR Dizin konu dizini; alan, grup veya konu adıyla filtrelenebilir)
bibliotr subjects Tıp
# --facet subject=... tam değer, konu adı ("spor bilimleri") ya da grup ("Tıp") alır;
# grup verilirse altındaki tüm konular aranır. Yanlış yazımda en yakın konular önerilir.

# Bir konudaki tüm yayınları çek
bibliotr harvest --facet "subject=Fen > Tıp > Spor Bilimleri" --dataset spor --years 2000-2026

# Tüm sonuçları çek (yıl yıl bölerek) ve "biblio" veri setine kaydet
bibliotr harvest "bibliometri" --dataset biblio \
    --facet database=SOCIAL --facet publicationLanguage=TUR --facet publicationLanguage=ENG \
    --years 2010-2025

# Sonradan eksik kaynakça/atıf ayrıntılarını tamamla
bibliotr enrich --dataset biblio

# Tek yayının tam kaydı
bibliotr detail 1163868

# Birden çok sorgunun veri setlerini tekilleştirerek birleştir, örtüşme tablosunu göster
bibliotr merge fa fa_tr fa_en --out ortusme.csv

# Veri setini OpenAlex'te bul (DOI, sonra başlık + yıl) ve güncel atıf sayılarını sakla
bibliotr openalex --dataset biblio --mailto ad@universite.edu.tr

bibliotr datasets
bibliotr stats --dataset biblio

# Dışa aktarma
bibliotr export --dataset biblio --format wos  --out biblio_wos.txt
bibliotr export --dataset biblio --format xlsx --out biblio.xlsx
bibliotr export --dataset biblio --format vosviewer --network keyword --out biblio_kw.txt

# Eşanlamlılar sözlüğü: önerileri listele, numarayla kabul et veya ayrı tut
bibliotr thesaurus suggest --dataset biblio --field keyword
bibliotr thesaurus suggest --dataset biblio --field keyword --accept 0,2,5 --reject 1
bibliotr thesaurus export --format vosviewer --field keyword --out thesaurus_terms.txt

# DOI / ORCID / ISSN ile doğrudan kayıt
bibliotr lookup doi 10.xxxx/yyyy
```

Facet adları dokümandaki gibidir, `facet-` ön eki isteğe bağlıdır. Aynı facet birden çok kez
verilirse çoklu seçim (VEYA) yapılır. Tüm facet'ler ve sıralama seçenekleri `bibliotr/client.py`
içindeki `FACETS` ve `ORDERS` sözlüklerindedir.

### Python / Jupyter içinden

```python
from bibliotr import TRDizinClient, Store
from bibliotr import analysis

client = TRDizinClient(min_interval=1.0)
records = client.harvest("bibliometri", facets={"database": "SOCIAL"}, years=range(2015, 2026))

store = Store("data/trdizin.db")
store.add_records(store.create_dataset("biblio", "bibliometri"), records)

pubs = store.publications("biblio")
analysis.summary(pubs)
analysis.top_counts(pubs, "journal", 20)
g = analysis.field_network(pubs, "author", min_occurrence=2)
```

## Diğer araçlarla kullanım

- **Biblioshiny (R, bibliometrix)**: `--format wos` ile oluşan dosyayı *Import raw file(s) →
  Web of Science → Plaintext* ile yükleyin.
- **VOSviewer**: WoS dosyasını *Create map based on bibliographic data → Web of Science* ile açın
  ya da `--format vosviewer` ile üretilen `_map.txt` + `_network.txt` dosyalarını
  *Create map based on network data* ile açın.
- **Zotero / Mendeley / EndNote**: RIS veya BibTeX.

Not: Ortak atıf (co-citation) ve bibliyografik eşleşme analizleri kaynakça verisi gerektirir.
TR Dizin arama yanıtında kaynakça yoksa WoS dosyasındaki `CR` alanı boş kalır.

### WoS dosyası bibliometrix için nasıl hazırlanıyor

WoS dosyası (`bibliotr/wos.py`) bibliometrix'in okuma kurallarına (`isi2df`, `metaTagExtraction`,
`histNetwork`) göre üretilir:

- **RP** (zorunlu alan) yazılır. TR Dizin'de sorumlu yazar bilgisi olmadığı için **ilk yazar**
  kullanılır; "Corresponding Author's Countries" analizi bu yüzden ilk yazarın ülkesini gösterir.
- **C1**: her adres ayrı satırda, `[Yazarlar] KURUM UNIV, Şehir, Turkey.` biçimindedir. Ülke
  bibliometrix'in ülke listesindeki İngilizce adıyla yazılır; adreslerin içindeki noktalar
  ("T.C." gibi) temizlenir.
- **C3**: TR Dizin'in standart kurum adları. Bibliometrix kurum analizinde bunu kullanır.
- **CR**: kaynaklar `SOYAD I, YIL, KAYNAK, Vcilt, Psayfa, DOI` biçimine çevrilir. Aynı eser her
  yayında aynı dizgiyle yazılır (ortak atıf için). Veri setindeki bir yayına yapılan atıf, o
  yayının bibliometrix etiketiyle birebir yazılır (yerel atıf / historiograph için). DOI'si
  olmayan yayınlar bibliometrix'te yerel atıf alamaz.
- **DE / ID**: DE yazar anahtar kelimeleridir (TR, EN veya ikisi). TR Dizin'de Keywords Plus
  olmadığı için ID alanına varsayılan olarak İngilizce anahtar kelimeler yazılır.
- Türkçe karakterler varsayılan olarak ASCII'ye çevrilir (`--wos-unicode` ile korunur).

```bash
bibliotr export --dataset biblio --format wos --out biblio_wos.txt --wos-keywords en
bibliotr wos-check biblio_wos.txt   # bibliometrix'in göreceği ülke, kurum, kaynak ve yerel atıf özeti
```

### Eşanlamlılar sözlüğü (yazar, kurum, anahtar kelime birleştirme)

Aynı birimin farklı yazımları ("bibliyometri" / "bibliometri", "Hacettepe Üniversitesi" /
"Hacettepe Univ.") ayrı sayılmasın diye onaylanmış eşleşmeler bir sözlükte tutulur
(`bibliotr/thesaurus.py`). Sözlük tüm veri setleri için ortaktır, ham veriyi değiştirmez ve
arayüzdeki analizlere, `stats` ve `export` komutlarına uygulanır (`--raw` ile kapatılır).

- **Öneriler kendiliğinden uygulanmaz.** *Eşanlamlılar* sekmesinde ya da `thesaurus suggest` ile
  listelenir; kabul edilenler sözlüğe eklenir, *ayrı tut* denenler bir daha önerilmez.
- **Anahtar kelime ve kurum**: noktalama, büyük/küçük harf, Türkçe karakter, kısaltma (Üniv.,
  Fak., Hast., T.C.) ve çoğul eki farkları; benzer yazılış (eşik ayarlanabilir); anahtar
  kelimelerde aynı yayında aynı sırada verilen Türkçe-İngilizce karşılıklar (en az 2 yayında).
  Aynı yayında birleşen terimler bir kez sayılır.
- **Yazar**: uygulama yazarları zaten TR Dizin yazar kimliğiyle sayar. Öneriler aynı ORCID'e
  sahip farklı profiller, kimliksiz kayıt ile aynı ad ve aynı ad + ortak kurum durumlarıdır.
- **Aynı kısa adlı farklı yazarlar**: bibliometrix ve VOSviewer yazarları adla tanıdığı için
  "Yilmaz, A" adlı farklı kişiler orada tek kişi sayılır. WoS dışa aktarımı bu kişileri tam
  adlarıyla, tam ad da aynıysa sıra numarasıyla ("Yilmaz, Ali 2") yazar
  (`--wos-same-names` ile kapatılır). `bibliotr thesaurus homonyms` bu yazarları listeler.
- **Dosyalar**: JSON (başka projeye taşımak için), VOSviewer thesaurus (`label` / `replace by`)
  ve bibliometrix `synonyms` listesi (`HEDEF;VARYANT;...`). Uygulamadan aktarılan WoS dosyasında
  sözlük zaten uygulanmıştır; bu dosyalar başka kaynaklardan gelen veriler içindir.
- `paper/` betikleri sözlüğü uygulamaz; dondurulmuş veri setinin sonuçları değişmez.

**OpenAlex atıf sayıları.** TR Dizin her kayıt için bir OpenAlex atıf sayısı bildirir
(`otherCitations`). 162 kayıtlık bir sınamada bu sayı, OpenAlex'in kendisinde atıf almış görünen
108 kayıtta sıfırdı ve hiçbir kayıtta OpenAlex'in sayısından yüksek değildi. Güncel sayılar için
`bibliotr openalex` komutunu (ya da *Kaynakça ve Atıf* sekmesindeki düğmeyi) kullanın; karşılaştırma
tablosu "OpenAlex (TR Dizin)" ve "OpenAlex (doğrudan)" satırlarını yan yana gösterir. Başlıkla
eşleştirmede aday, katlanmış başlık benzerliği en az %92 ve yıl farkı en çok 1 ise kabul edilir.
OpenAlex istek başına ücretlendirir: API anahtarı olmadan günlük bütçe yaklaşık 100 başlık
aramasına yeter (DOI sorguları çok daha ucuzdur). Ücretsiz anahtarı <https://openalex.org/settings/api>
adresinden alıp `--api-key` ya da `OPENALEX_API_KEY` ortam değişkeniyle verin. Bütçe dolarsa o ana
kadar bulunanlar saklanır; yeniden çalıştırınca yalnızca kalanlar aranır.

## Veri kaynağı ve alanlar

| Uç nokta | İçerik |
|---|---|
| `api/defaultSearch/publication/?q=...` | Arama sonuçları (sayfalı) |
| `api/publicationById/{id}` | Tek yayının tam kaydı: kaynakça (`references`), bu yayına atıf yapan TR Dizin yayınları (`citedReferences`), CrossRef / OpenAlex / OpenCitations atıf sayıları (`otherCitations`) |
| `api/journalById/{id}` | Dergi kaydı: ad, diğer ad, ISSN, editör, dizinlendiği yıllar (`journalYear`: yayın ayları, konular, eksik sayı/hakem raporu), reddedildiği yıllar. Yayınları içermez; derginin yayınları dergi adıyla (`facet-journalName`) aranıp `journal.id` ile süzülür |
| `api/public/yayin/doi`, `yazar/orcid`, `dergi/issn` | DOI / ORCID / ISSN ile doğrudan kayıt |

Arama yanıtı kaynakçayı (`references`) içerir, ancak yayına atıf yapan TR Dizin yayınları
(`citedReferences`) `null` gelir. Bu yüzden `--details auto` (varsayılan) yalnızca atıf almış
yayınlar için `publicationById` isteği atar (her biri yaklaşık 1 saniye).

Arama yanıtındaki `aggregations` alanı, tüm sonuç kümesinin facet dağılımlarını verir.
`bibliotr profile` ve arayüzdeki "Hızlı profil" bunu kullanır: veri çekmeden binlerce yayının
yıl, dergi, kurum, şehir, konu ve yazar dağılımı tek istekte görülür. Yazar listesi gibi bazı
dağılımlar kesilmiş olabilir (listede olmayan belge sayısı gösterilir).

Arama motoru en fazla 10.000 kayda sayfalama izni verir; daha büyük kümelerde `--years` kullanın.

Alan eşlemesiyle ilgili notlar (`bibliotr/normalize.py`):

- **Başlık** `abstracts[].title` alanından yayın diline göre alınır. `orderTitle` sıralama içindir ve
  boşluk kaybı olabilir (ör. "BİRSOSYAL").
- **Yazarlar** `order` alanına göre sıralanır; yanıtta ters sırada gelebilirler.
- **Kurum**: TR Dizin'in eşleştirdiği üst kurum (`institution.rootTitle`, ör. üniversite) kullanılır;
  alt birim (fakülte, anabilim dalı) ayrıca `unit` alanında tutulur. Bir yazarın birden çok kurumu
  olabilir. Eşleşmemiş ("Tanımlanmamış Kurum", "Yabancı Kurumlar") yazarlarda kurum, şehir ve ülke
  ham adresten (`institutionName`: "Kurum, Fakülte, Bölüm, Şehir, Ülke") tahmin edilir. Yurt içi
  eşleşmiş kurumlarda ülke alanı boş geldiği için adresten ya da varsayılan olarak "Türkiye" alınır.
- **Kaynak eşleştirme** (ortak atıf için): kaynak TR Dizin'deyse `targetPublication` kimliğiyle,
  metinde DOI varsa DOI ile kesin; değilse ilk yazar soyadı + yıl + başlığın ilk kelimeleriyle
  sezgisel olarak yapılır (APA ve Vancouver biçimleri tanınır).

Bir alan boş kalırsa `bibliotr inspect "sorgu"` ile ham yapıyı görün, `normalize.py` başındaki
ilgili `*_KEYS` listesini güncelleyin ve `bibliotr reindex` çalıştırın. Ham JSON saklandığı için
yeniden veri çekmek gerekmez.

## Sorumlu kullanım

- İstekler arasında varsayılan olarak 1 saniye beklenir (`--delay`), 429/5xx hatalarında üstel
  geri çekilmeyle yeniden denenir. Bu değeri düşürmeyin.
- Toplu veri çekmeden önce TR Dizin / ULAKBİM kullanım koşullarını kontrol edin ve
  yayınlarınızda veri kaynağını belirtin.
- Çok geniş sorgularda `--years` ile yıllara bölmek hem derin sayfalama sınırlarını aşmayı
  hem de kesilen bir çekimi daha küçük parçalarla tekrarlamayı kolaylaştırır.

## Proje yapısı

```
bibliotr/
  client.py     API istemcisi: arama, sayfalama, facet'ler, hız sınırı, DOI/ORCID/ISSN
  normalize.py  Ham kayıt -> Publication (alan adı adayları burada)
  storage.py    SQLite: veri setleri, yayınlar, yazarlar, anahtar kelimeler, ham JSON
  analysis.py   Özet, dağılımlar, yazar metrikleri, Lotka, Bradford, ağlar, tematik evrim
  export.py     CSV, Excel, WoS, RIS, BibTeX, VOSviewer
  openalex.py   OpenAlex ile DOI ve başlıkla doğrudan eşleştirme, güncel atıf sayıları
  thesaurus.py  Eşanlamlılar sözlüğü: birleştirme önerileri, uygulama, VOSviewer / bibliometrix dosyaları
  wos.py        Bibliometrix kurallarına uygun WoS dışa aktarımı ve uyumluluk denetimi
  cli.py        `bibliotr` komutu
app.py          Streamlit arayüzü
tests/          pytest testleri (sentetik örnek yanıtla)
```

## Testler

```bash
pytest
```

`tests/fixtures/publication_by_id.json` ve `tests/fixtures/search_subject.json` gerçek TR Dizin
yanıtlarıdır (kısaltılmış).
`tests/fixtures/publication_search.json` ise yedek alan adlarını sınamak için sentetik bir örnektir.
