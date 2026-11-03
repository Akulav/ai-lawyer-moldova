<p align="center">
  <img src="docs/assets/banner.svg" alt="Avocat AI Moldova: asistent juridic AI local pentru legislația Republicii Moldova" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Akulav/ai-lawyer-moldova/releases/latest"><img alt="Ultima versiune" src="https://img.shields.io/github/v/release/Akulav/ai-lawyer-moldova?style=flat-square&color=0b3a6f"></a>
  <img alt="Windows 10/11 x64" src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0b3a6f?style=flat-square">
  <img alt="AI local" src="https://img.shields.io/badge/AI-local-e6c563?style=flat-square">
</p>

<p align="center"><a href="README.md">English</a> · <b>Română</b></p>

---

**Avocat AI Moldova** este un asistent AI local de informare juridică pentru legislația Republicii Moldova. Întrebați în cuvinte simple și primiți un răspuns cu trimiteri la articole și linkuri către surse. Modelul, indexul și discuțiile rămân pe calculatorul dvs.

> [!WARNING]
> **Nu este consultanță juridică și nu este un avocat.** Răspunsurile sunt generate automat și pot fi greșite, incomplete sau depășite. Verificați întotdeauna sursa citată și consultați un avocat autorizat pentru decizii importante.

## Pornire

**Cerințe:** Windows 10 sau 11 (x64), cel puțin 30 GB liberi pe disc și conexiune la internet la prima pornire.

1. Descărcați `AvocatAI.exe` din ultima [versiune publicată](https://github.com/Akulav/ai-lawyer-moldova/releases/latest).
2. Puneți-l într-un folder separat și porniți-l.
3. Browserul se deschide singur când programul este gata.

Nimic nu se instalează în sistem: totul rămâne în acel folder.

Programul rulează în bara de sistem, lângă ceas, nu într-o fereastră:

| Acțiune | Rezultat |
| --- | --- |
| Clic pe pictogramă | Deschide programul în browser. |
| Clic dreapta → **Deschide folderul programului** | Deschide folderul. |
| Clic dreapta → **Deschide jurnalul** | Deschide jurnalul. |
| Clic dreapta → **Repornește** | Repornește serverul. |
| Clic dreapta → **Ieșire** | Oprește serverul și motorul AI și eliberează memoria RAM și VRAM folosită de model. |

Motorul AI se oprește și dacă programul este închis altfel, de exemplu din Task Manager. Dacă porniți din nou fișierul `.exe` cât timp programul rulează, se deschide doar browserul.

Dintr-o copie a proiectului, `run.bat` pornește același program într-o fereastră de consolă. La prima pornire se descarcă motorul AI (circa 1,6 GB) și un model potrivit memoriei RAM (între 5,2 și 17,7 GB), iar interfața se deschide la <http://127.0.0.1:18917>. Dacă portul este ocupat, programul folosește următorul port liber.

Pentru a folosi programul de pe telefon, tabletă sau alt calculator din rețeaua locală ori prin VPN, activați **Acces din rețeaua locală sau VPN** în pagina **Setări** și apăsați **Repornește acum**. Pagina afișează adresele de deschis, de exemplu `http://192.168.1.20:18917`. Dacă Windows Firewall întreabă, permiteți accesul sau permiteți portul afișat. Dezactivarea setării refuză imediat celelalte dispozitive.

> [!CAUTION]
> Nu există autentificare: oricine ajunge la acest port poate folosi programul și vedea conversațiile, deci faceți asta doar în rețele de încredere.

## Ce face

- **Întreabă:** chat cu răspuns, trimiteri la articole și linkuri. Traseul prin arborele legislației e afișat pe măsură ce se parcurge.
- **Caută în lege:** căutare în titluri și text integral, cu filtre după tip, stare, domeniu și an.
- **Arborele legislației:** Ramură › Domeniu › Act de bază.
- **Index:** progresul indexării, pauză și reluare, import de texte consolidate.
- **Probleme:** edițiile care nu au putut fi procesate, cu reîncercare pentru una sau pentru toate.
- **Modele AI:** schimbați, ștergeți sau adăugați orice model Ollama ori GGUF. Încărcați un model în memorie, scoateți-l sau opriți complet motorul AI pentru a elibera toată memoria.

## Limite

Citiți-le înainte de a vă baza pe un răspuns.

- **Acoperirea nu este completă.** Sursa automată este Monitorul Oficial, cu ediții de după circa 2001. Actele mai vechi, de exemplu Codul fiscal din 1997, pot apărea doar ca titlu până când le importați.
- **Textele nu sunt consolidate implicit.** Textul indexat este cel publicat inițial. Modificările sunt legate ca acte separate, iar programul avertizează când o sursă este neconsolidată.
- **Starea este detectată automat.** „Abrogat” este dedus din textul altor acte și poate fi greșit. „Fără abrogare detectată” nu garantează că actul este în vigoare.
- **Modelele greșesc.** Se verifică doar existența trimiterilor, nu și raționamentul juridic.

[`legis.md`](https://www.legis.md) are redacțiile consolidate, dar blochează accesul automat, iar acest proiect nu ocolește blocarea. Pentru a adăuga un text consolidat: deschideți actul pe `legis.md`, apăsați **Pdf** și trageți fișierul în secțiunea Index.

## Confidențialitate

Întrebările și răspunsurile sunt procesate local și nu sunt trimise către niciun serviciu AI. Programul folosește internetul pentru a descărca legislația, modelele și componentele necesare.

## Documentație

Documentația tehnică este în limba engleză: [arhitectură](docs/ARCHITECTURE.md), [surse de date](docs/DATA_SOURCES.md), [contribuții](CONTRIBUTING.md). Configurarea și depanarea sunt descrise în [README.md](README.md).

## Licență

**Cod sursă disponibil, nu open source.** Puteți rula versiunile publicate și cele compilate de dvs., citi codul și îl puteți modifica pentru uz propriu. Versiunile modificate le puteți împărți doar privat și gratuit, cu un grup mic de prieteni sau membri ai familiei. Nu puteți publica sau redistribui programul ori versiuni modificate și nu puteți câștiga bani din el. Termenii compleți sunt în [LICENSE](LICENSE).
