# Test meeting: AI agents & adoption (Greek + English)

**How to use**
1. Read it out loud **naturally**, like a real meeting: fillers included, don't rush. Takes about 4–5 min.
2. Read both people (change your voice a little) or with a colleague.
3. Record: `make record` → Ctrl+C when you finish → it saves `sessions/my_take.wav`.
4. Score: `make score` → shows how many words each model got wrong.

> Only the lines starting with a **name:** are scored. Read the English words in English, like you normally would.

---

**Γιώργος:** Λοιπόν, καλημέρα σε όλους. Εεε, να ξεκινήσουμε; Σήμερα θέλω να μιλήσουμε για το AI adoption στην εταιρεία, και κυρίως για τα agents που δοκιμάζουμε.

**Μαρία:** Ναι, καλημέρα. Εμμ, δηλαδή, έχουμε ήδη ένα proof of concept που τρέχει τοπικά, on-prem, με ένα local LLM, το Gemma, πάνω σε ένα MacBook.

**Γιώργος:** Οκ. Και πώς πάει το latency; Γιατί οι stakeholders, ξέρεις, θέλουν να βλέπουν απάντηση σε λιγότερο από δύο δευτερόλεπτα.

**Μαρία:** Εεε, κάπως έτσι είναι, ας πούμε στα 40 tokens το δευτερόλεπτο. Το πρόβλημα είναι το context window. Όταν φτάνουμε τα 30.000 tokens, δηλαδή, αργεί αρκετά.

**Γιώργος:** Basically, δηλαδή, χρειαζόμαστε RAG με embeddings και όχι να του δίνουμε ολόκληρα τα documents μέσα στο prompt.

**Μαρία:** Ακριβώς. Βάζουμε τα embeddings σε PostgreSQL με pgvector, και το agent κάνει tool calling μέσω MCP για να βρει τα σωστά κομμάτια.

**Γιώργος:** Ωραία. Εμμ, τώρα, για το compliance. Το AI Act και το GDPR, actually, είναι το μεγαλύτερο ρίσκο. Ποιος κάνει το risk assessment;

**Μαρία:** Λοιπόν, εγώ ετοιμάζω ένα governance document με τα use cases, εεε, ποια είναι high risk και ποια όχι, και θα το στείλω στο legal μέχρι τις 15 Νοεμβρίου.

**Γιώργος:** Τέλεια. Και το budget; Είχαμε πει περίπου 3.000 ευρώ τον μήνα για cloud, για Azure OpenAI.

**Μαρία:** Ναι, αλλά, you know, αν τρέχουμε τοπικά, το κόστος πέφτει, ας πούμε, κατά 70 τοις εκατό. Το μόνο κόστος είναι το hardware.

**Γιώργος:** Οκ, ωραία. Εεε, και το deployment; Θα πάει σε VM ή σε Docker container;

**Μαρία:** Σε Docker, με ένα CI/CD pipeline στο GitHub Actions. Κάθε pull request περνάει από unit tests και μετά κάνουμε rollout πρώτα σε staging.

**Γιώργος:** Like, πόσο χρόνο χρειαζόμαστε για όλο αυτό; Ένα sprint ή δύο;

**Μαρία:** Εμμ, δηλαδή, ρεαλιστικά δύο sprints. Το πρώτο για το backend σε .NET και Blazor, και το δεύτερο για το dashboard με τα KPIs.

**Γιώργος:** Και τι KPIs θα μετράμε;

**Μαρία:** Λοιπόν, πόσες ώρες γλιτώνει η ομάδα την εβδομάδα, πόσα emails κάνει triage σωστά το agent, και το accuracy στις απαντήσεις. Στόχος, εεε, πάνω από 90 τοις εκατό.

**Γιώργος:** Ωραία. Και ένα τελευταίο. Το fine-tuning το χρειαζόμαστε ή φτάνει το prompt engineering;

**Μαρία:** Κατά τη γνώμη μου, προς το παρόν φτάνει το prompt engineering με few-shot examples. Το fine-tuning, ξέρεις, θέλει dataset και GPU, και είναι overkill για τώρα.

**Γιώργος:** Συμφωνώ. Οκ, λοιπόν, να κλείσουμε. Action items: η Μαρία στέλνει το governance document, εγώ ανοίγω τα tickets στο Jira για το backlog, και ξαναβρισκόμαστε την Πέμπτη στις 10 το πρωί.

**Μαρία:** Τέλεια, ευχαριστώ. Καλή συνέχεια σε όλους!

---

**What we measure**
- **Overall error**: % of words wrong.
- **Tech words**: did it write `agents`, `RAG`, `PostgreSQL`... correctly in English, or turn them into Greek letters (`έιτζεντς`)?
- **Fillers**: does it keep `λοιπόν / δηλαδή / you know`? Does it drop `εεε / εμμ`? Dropping `εεε` is OK, even good.
- **Numbers**: 15 Νοεμβρίου, 3.000 ευρώ, 70 τοις εκατό, 10 το πρωί.
- **Speed**: how many seconds per minute of audio.
