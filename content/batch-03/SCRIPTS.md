# Batch 03: PHP2Shell news response (2026-10-22)

**Topic:** this week's critical PHP flaws ("PHP2Shell") and why updating an existing website matters.
**Format:** 9:16, 30 s or longer, your cloned voice (narrated), kinetic captions, news-style motion-graphic cards mixed with smooth drone B-roll. No camera footage.
**Accounts:** both brands belong to Elevate Vision Digital and post to their own accounts: albertaweb.design and @buildwithdb.

## Verified facts used (keep claims to these)
- Two critical flaws in **PHP**, rated **9.2/10** (CVSS v4), allow **remote code execution without logging in**. They were nicknamed **PHP2Shell**, and the bug dates back to **2012**. Sources: BankInfoSecurity, ABC News (AU).
- PHP runs **about 7 in 10 websites**, including **WordPress**, Drupal, Joomla and Magento, plus shared hosting and control panels. Sources: CBC, ABC, BankInfoSecurity.
- Found **with the help of AI**. Sources: ABC, TechCrunch, Nextgov.
- **Timeline:**
  - Amazon saw **exploit attempts from Oct 17**, before disclosure.
  - Details **leaked Oct 20**.
  - The **patch was released Oct 21**.
  - Sources: The Register, BankInfoSecurity, ABC.
- **Cloudflare CEO Matthew Prince:** *thousands* of attempts in the 10 hours before the announcement, *tens of thousands* within 45 minutes of disclosure, *hundreds of thousands* within two hours, then *millions*. Sources: Reuters via PYMNTS, Global Banking & Finance, Nextgov, the Guardian.
- **Government and service response:**
  - Government sites went **offline** in several countries.
  - **Banking, transport, tax and government services** were suspended as a precaution.
  - The **UK NCSC** and **US NSA** said to **update immediately or take systems offline**.
  - Sources: TechCrunch, CBC, Nextgov.
- **Canada, a separate breach:** a third-party vendor breach exposed **1.5 million Crime Stoppers tips**. Sources: CBC, Oct 21.

**Visual markup:** `[CARD type: text | sub]` on its own line under a beat replaces that beat's drone shot with a motion-graphic card. Beats with no card use smooth drone footage. Card types:
- **headline:** a news-style headline card with a source credit line.
- **stat:** a big animated number with a label.
- **alert:** a flashing red/amber warning banner.
- **terminal:** scrolling code or log lines with an "exploit attempt" counter ticking up.
- **timeline:** dated steps that reveal in order.

No outlet logos; sources appear as text credits.

---

## AlbertaWeb.Design (3 narrated + 3 B-roll)

### AW31 · AD · "Is your site running the hacked code?"
- **Hook overlay:** Is your website running this week's hacked code?
- **B-roll:** aerial Calgary skyline · city at night · highway

> This week, two critical flaws were found in *PHP*, the code behind roughly seven in ten websites, including WordPress.
[CARD stat: 7 in 10 | websites run PHP, including WordPress]
>
> They let attackers take over a website's server without ever logging *in*.
[CARD headline: Critical PHP flaws let attackers take over servers | Severity 9.2 / 10 · Source: BankInfoSecurity]
>
> Attackers were already trying them before the fix was even *released*.
>
> Governments pulled their own websites *offline* until they could patch.
[CARD alert: UPDATE NOW OR GO OFFLINE | Advice from UK NCSC and US NSA · Oct 21]
>
> If your website hasn't been updated in years, it's an open *door*.
>
> We update and harden existing Alberta websites, without starting over. Run the live scan at albertaweb dot design.

- **Post:** This week's PHP flaws hit the code behind ~7 in 10 websites. When was yours last updated? 🔐 Live scan at albertaweb.design #cybersecurity #wordpress #websitesecurity #albertabusiness #php2shell
- **Music:** Sombra do Crime.m4a (tense)

### AW32 · ORG · "Millions of attacks in hours"
- **Hook overlay:** From thousands to millions of attacks. In hours.
- **B-roll:** night city aerial · lit highway · downtown at night

> Here's how fast a website attack *spreads*.
[CARD timeline: Oct 17 attacks begin · Oct 20 details leak · Oct 21 patch released | PHP2Shell timeline]
>
> According to Cloudflare's CEO, there were *thousands* of attempts before the flaw was even announced.
[CARD terminal: exploit attempts | counter 1,000 → 10,000 → 100,000 → 1,000,000+]
>
> Tens of thousands within forty five minutes. Hundreds of thousands within two *hours*.
>
> Then *millions*.
[CARD stat: Millions | exploit attempts after disclosure · Source: Cloudflare]
>
> Attackers don't wait for you to get around to it. Updates have to happen in hours, not *months*.
>
> And if your website runs on hosting nobody is watching, nobody is applying that patch for *you*.
>
> Follow for plain English website security for Alberta businesses.

- **Post:** Thousands → tens of thousands → millions of attacks within hours of disclosure (Cloudflare). Speed is security ⏱️ #cybersecurity #websitesecurity #smallbusiness #calgary #php2shell
- **Music:** none (voice-only, add trending audio)

### AW33 · AD · "Update, don't rebuild"
- **Hook overlay:** Your old website doesn't need replacing. It needs updating.
- **B-roll:** aerial construction site · houses · skyline

> After a week like this, you might think you need a brand new *website*.
>
> Usually you don't. You need the one you have *updated*.
[CARD headline: Governments worldwide rush to patch PHP flaws | Source: Nextgov · Oct 22]
>
> Current software. Patched plugins. A secure *server*. Backups that actually work.
[CARD alert: CHECKLIST | Updated PHP · Patched plugins · Secure hosting · Tested backups]
>
> Old WordPress sites on outdated hosting are exactly what attackers *scan* for.
>
> We retrofit existing sites so they're secure, fast, and readable by Google and *AI*.
>
> Book a consult at albertaweb dot design, or call eight two five, four three five, nine zero nine eight.

- **Post:** Don't rebuild. Update. 🔧 We retrofit existing Alberta websites so they're secure and fast. albertaweb.design #wordpresssecurity #websitemaintenance #albertabusiness #edmonton #calgary
- **Music:** ten_minute_reset.mp3

### AlbertaWeb B-roll (≥32 s, smooth drone footage, kinetic text line)
| ID | Text line | Audio | Post |
|---|---|---|---|
| AW-B24 | 7 in 10 websites run PHP. Is *yours* patched? | heat_on_the_north-side.mp3 | This week's PHP flaws affect the code behind most websites. Is yours updated? 🔐 albertaweb.design #cybersecurity #wordpress #php2shell #albertabusiness #calgary |
| AW-B25 | Update or go *offline* | silent | Governments took sites offline this week to patch. Small businesses can't afford to. 🔐 albertaweb.design #websitesecurity #cybersecurity #smallbusiness #alberta #php2shell |
| AW-B26 | Old site? *Update* it. | 432 Hz Rooftop.m4a | Your existing website can be secured without starting over 🔧 albertaweb.design #websitemaintenance #wordpress #albertabusiness #edmonton #cybersecurity |

---

## Build With DB (3 narrated + 3 B-roll)

### DB31 · ORG · "AI found it. AI attacks it."
- **Hook overlay:** AI found a hole in most of the internet
- **B-roll:** aerial city · highway · night skyline

> This week, *AI* helped uncover two critical flaws in PHP, the code behind most of the internet.
[CARD headline: AI helps uncover critical PHP flaws hidden since 2012 | Source: ABC News · Oct 22]
>
> The bug had been sitting there since *twenty twelve*.
>
> But here's the part that matters. Attackers were already using it before the patch *existed*.
[CARD terminal: exploit attempts | counter 1,000 → 10,000 → 1,000,000+]
>
> The same tools that find holes faster can also *exploit* them faster.
>
> So the gap between a flaw and an attack is shrinking to *hours*.
>
> Your systems need to keep up. Systems create freedom. Follow if you're building.

- **Post:** AI found a 14-year-old hole in the code behind most websites. Attackers moved within hours 🤖 #ai #cybersecurity #automation #buildwithdb #php2shell
- **Music:** none (voice-only)

### DB32 · ORG · "This week proved Protect"
- **Hook overlay:** This week is why 'Protect' is one of my principles
- **B-roll:** calm aerial · river valley · city

> One of my six principles is *protect*. This week showed why.
>
> Critical flaws in PHP sent governments scrambling to take websites *offline*.
[CARD alert: UPDATE NOW OR GO OFFLINE | Advice from UK NCSC and US NSA · Oct 21]
>
> Banking, transport, and tax services were *paused* in several countries.
[CARD stat: Millions | exploit attempts within hours · Source: Cloudflare]
>
> Here in Canada, a vendor breach exposed one and a half million Crime Stoppers *tips*.
>
> Everything you build depends on the software underneath it. Your website, your apps, your tools. Keep them *updated*, and keep backups you've actually tested.
>
> Build it. Then protect it. Follow for more.

- **Post:** Build it. Then protect it. This week proved why 🛡️ #cybersecurity #buildwithdb #systemscreatefreedom #smallbusiness #canada
- **Music:** none (voice-only)

### DB33 · AD · "Make updates a system"
- **Hook overlay:** Updates shouldn't depend on your memory
- **B-roll:** aerial highway · city grid · night roads

> Most security problems start with an update nobody got around *to*.
>
> This week, attackers went from thousands to millions of attempts in *hours*.
[CARD timeline: Before disclosure: thousands · 45 minutes: tens of thousands · 2 hours: hundreds of thousands · Then: millions | Source: Cloudflare CEO]
>
> You can't rely on remembering to check, especially when you're busy running jobs all week. It has to be a *system*.
>
> Update reminders, automatic backups, and alerts when something *changes*.
[CARD terminal: system check | ✔ backups ✔ updates ✔ alerts]
>
> I build automations that take the busy work out of keeping your business *safe*.
>
> Request a build at buildwithdb dot ca.

- **Post:** Security shouldn't depend on your memory. Make it a system ⚙️ @buildwithdb #automation #cybersecurity #businesssystems #smallbusiness #buildwithdb
- **Music:** Dark Gold Grind.m4a

### Build With DB B-roll (≥32 s)
| ID | Text line | Audio | Post |
|---|---|---|---|
| DB-B24 | *Protect* what you build | Grief and Glory.m4a | Build it. Then protect it. 🛡️ @buildwithdb #buildwithdb #cybersecurity #systemscreatefreedom #protect #dronefootage |
| DB-B25 | Updates are a *system* | silent | Security that runs on a system, not memory ⚙️ @buildwithdb #automation #cybersecurity #buildwithdb #systems #smallbusiness |
| DB-B26 | AI found it *first* | Sombra do Crime.m4a | AI found the flaw. Attackers moved within hours. Is your stack updated? 🤖 @buildwithdb #ai #cybersecurity #buildwithdb #php2shell #automation |
