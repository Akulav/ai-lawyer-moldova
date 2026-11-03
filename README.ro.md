# Avocat AI Moldova

Asistent AI local de informare juridică pentru legislația Republicii Moldova. Întrebați în cuvinte simple și primiți un răspuns cu trimiteri la articole și linkuri către surse. Modelul, indexul și discuțiile rămân pe calculatorul dvs.

[Read in English](README.md)

> [!WARNING]
> **Nu este consultanță juridică și nu este un avocat.** Răspunsurile sunt generate automat și pot fi greșite, incomplete sau depășite. Verificați întotdeauna sursa citată și consultați un avocat autorizat pentru decizii importante.

## Pornire

**Cerințe:** Windows 10 sau 11 (x64) și conexiune la internet la prima pornire.

Rularea necesită permisiunea scrisă a titularului drepturilor (vedeți secțiunea Licență). Cu această permisiune, descărcați `AvocatAI.exe` din ultima [versiune publicată](https://github.com/Akulav/ai-lawyer-moldova/releases/latest), puneți-l într-un folder separat cu cel puțin 30 GB liberi și porniți-l. Nimic nu se instalează în sistem: totul rămâne în acel folder.

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

Pentru a folosi programul de pe alte dispozitive din rețeaua locală sau prin VPN, porniți-l cu `run-lan.bat` și permiteți portul 18917 în firewall. Fereastra programului afișează adresele de deschis, de exemplu `http://192.168.1.20:18917`. Nu există autentificare: oricine ajunge la acest port poate folosi programul și vedea conversațiile, deci faceți asta doar în rețele de încredere.

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

**Doar pentru vizualizare, nu este open source.** Puteți citi codul, dar nu îl puteți rula, copia, modifica sau distribui fără permisiune scrisă. Detalii în [LICENSE](LICENSE).
