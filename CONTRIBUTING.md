CONTRIBUTING — spôsob práce s repozitárom (SK)

Tento dokument popisuje odporúčaný pracovný postup pri vývoji, commitovaní a pushovaní zmien v tomto projekte, vrátane nastavenia a riešenia problémov s nástrojmi Black, Ruff, Mypy a .pre-commit-config.yaml.

1. Základné princípy

- Cieľom je udržiavať konzistentný štýl kódu, automatické lintovanie a kontrolu typov ešte pred committom a v CI.
- Používame framework pre-commit, ktorý spúšťa preddefinované "hooky" (formátovače, lintre, checkery) pri commite alebo manuálne.
- V repozitári sú pinned (fixované) verzie hookov, aby správanie bolo reprodukovateľné medzi vývojármi a CI.

2. Požiadavky (lokálne)

- Python 3.11/3.12 v izolovanom prostredí (venv).
- Odporúčané inštalovať nástroje: pre-commit, black, ruff, mypy.
  Napríklad:
  - python -m pip install --upgrade pip
  - python -m pip install pre-commit black ruff mypy
- Po sklonovaní repozitára spustiť:
  - pre-commit install --install-hooks
  Tento príkaz nainštaluje hooky definované v .pre-commit-config.yaml.

3. Hlavné konfiguračné súbory

- .pre-commit-config.yaml — definuje, ktoré hooky sa majú spúšťať, odkial sa inštalujú a s akými argumentmi alebo v ktorých stage-och.
- pyproject.toml / ruff.toml / mypy.ini — lokálna konfigurácia Black, Ruff a Mypy (line-length, ignorovania, cieľový Python a pod.).
- ~/.cache/pre-commit/pre-commit.log — log pri inštalácii/beh hookov (pri chybách).

4. Popis nástrojov a ich správanie

Black
- Deterministický formátovač kódu. Automaticky upraví zdrojový kód do jednotného štýlu.
- Lokálne: black --line-length=100 .
- V pre-commit: obvykle beží pri commite. Ak sú súbory neformatované, hook môže zlyhať a commit sa neodošle, kým sa formátovanie neaplikuje.

Ruff
- Rýchly linter a autofixer. Kontroluje štýl aj niektoré chyby.
- Lokálne: ruff check .  (na opravu: ruff --fix .)
- V pre-commit: spúšťa sa na staged alebo všetkých súboroch, podľa nastavenia.

Mypy
- Statická typová kontrola. Môže byť hlasitý a pomalší pri kontrole celého repozitára.
- V tomto projekte je mypy nakonfigurovaný v pre-commit ako "manual" (nespúšťa sa automaticky pri každom commite).
- Odporúča sa spúšťať mypy lokálne alebo v CI: mypy --strict src

YAML (.pre-commit-config.yaml)
- Definuje zdroje hookov (repo), rev (pinned verzia), id hooku a parametre (args, files, stages).
- Pinovanie rev zabezpečuje, že všetci používajú rovnakú logiku hookov.
- Stage "manual" znamená, že hook sa spustí len explicitným príkazom (pre-commit run --hook-stage manual).

5. Odporúčaný lokálny workflow

1. Vytvorte feature branch: git checkout -b feature/meno
2. Vyvíjajte a pravidelne commitujte malé zmeny.
3. Pred committom upravte a skontrolujte kód:
   - python -m black --line-length=100 .
   - ruff check .   (alebo ruff --fix . ak chcete automaticky opravovať)
4. Voliteľne spustiť mypy na zmenené moduly alebo celé src:
   - mypy --strict src
   Alebo spustiť cez pre-commit manuálne: pre-commit run --hook-stage manual --all-files
5. Stagujte súbory: git add <cesty>
6. Commit: git commit -m "krátky popis"
   - pre-commit automaticky spustí nakonfigurované hooky (okrem tých v stage: manual).
   - Ak hook zlyhá, opravte chyby a zopakujte kroky 3–6.
7. Push: git push origin feature/meno
8. Otvorte pull request; CI spustí rovnaké kontroly (pre-commit / mypy / pytest) a merge sa povolí až po ich úspechu.

6. Riešenie bežných problémov

- Chyba: "pre-commit checkout/checkout failed"
  - Dôvod: nemožno stiahnuť alebo checkoutnúť zadané repo/rev. Skontrolujte internetové pripojenie a že v .pre-commit-config.yaml je platný repo a rev.

- Chyba: "unrecognized subcommand" alebo ruff považuje cesty za subkomandu
  - Dôvod: nesprávne nastavenie hooku (entry/args). Používame oficiálne remote hooky, takže tento problém by nemal nastať. Ak áno, porovnajte s oficiálnou dokumentáciou ruff/ pre-commit.

- Chyba: pre-commit hook zobrazuje veľa výstupu (napr. mypy)
  - Riešenie: spustiť mypy lokálne a riešiť chyby postupne; prípadne použiť ignore_missing_imports pre tretie strany, alebo spustiť mypy ako manual alebo len pre vybrané moduly.

- Chyba pri commite a chcete urgentne commitnúť
  - Dočasne preskočte hooky: git commit --no-verify
  - Poznámka: používať len výnimočne, opravy dohánať neskôr.

7. Riešenie problémov s inštaláciou hookov

- Ak pre-commit hlási chyby pri inštalácii (checkout, tag not found), spustiť:
  - pre-commit clean
  - pre-commit install --install-hooks
- Pre detailné chyby pozrite log: cat ~/.cache/pre-commit/pre-commit.log

8. CI (nástroje a odporúčané kroky)

- CI by mal spúšťať rovnaké kontroly (pinned hook revs / Black / Ruff). Dôvod: rovnaké výsledky lokálne a v CI.
- Odporúčaný poriadok v CI:
  1) pre-commit run --all-files
  2) pre-commit run --hook-stage manual --all-files   # spustí mypy, ak chcete
  3) pytest -q --maxfail=1 --disable-warnings

9. Odporúčané best-practices

- Pred committom spúšťajte Black a Ruff, aby ste minimalizovali chyby pri commite.
- Používajte malé, tematické commity s jasnými správami.
- Pravidelne synchronizujte svoj branch s main / develop, aby ste znížili konflikty.
- Pridať nový test pre každú funkčnú zmenu.
- Ak riešite veľké zmeny, rozdeliť ich do menších PR-ov.

10. Užitočné príkazy — rýchly prehľad

- pre-commit install --install-hooks      # nainštalovať hooky po klonovaní
- pre-commit run --all-files             # spustiť všetky hooky na všetkých súboroch
- pre-commit run --hook-stage manual --all-files   # spustiť manuálne hooky (mypy)
- black --line-length=100 .              # formátovať kód
- ruff check .                           # spustiť ruff na projekte
- ruff --fix .                           # pokúsiť sa automaticky opraviť chyby ruff
- mypy --strict src                      # spustiť mypy s prísnymi pravidlami
- git commit --no-verify                 # preskočiť pre-commit hooky pri commite (použiť opatrne)

11. Pridanie nového hooku alebo zmena konfigurácie

- Upraviť .pre-commit-config.yaml a pridať / zmeniť príslušný repo / rev / id / args.
- Po zmene spustiť: pre-commit install --install-hooks
- Overiť lokálne: pre-commit run --all-files

12. Kontakt a ďalšie kroky

- Ak narazíte na problém, ktorý neviete vyriešiť, otvor issue v repozitári alebo kontaktujte maintainerov (v readme/PR popise sú kontakty).
- Ak chcete, môžem automaticky generovať CONTRIBUTING.md v inej podobe (kratší checklist, alebo kompletný .docx). Stačí povedať.

Ďakujeme za príspevok a dodržiavanie postupov — pomáha to udržiavať kvalitu projektu a hladký vývojový cyklus.
