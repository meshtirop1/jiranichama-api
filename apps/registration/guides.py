"""How a chama registers with the government, country by country, and the papers it
needs (owner request 2026-10-20: "lead every law in details, documents required, and make
it easy for them to prepare").

What is here is a plain-language guide, written from the public rules of each country's
registering office. Fees and forms change and differ between counties and districts, so
every guide says "about" and tells the group to confirm at the office. It is guidance,
not legal advice; the app says so.

Papers are of two kinds:
- ``app``: JiraniChama fills them from what it already knows (the chama's name, rules,
  members and officials) and leaves blanks only for what it must not hold (ID numbers,
  signatures, the meeting place).
- ``you``: the group brings them (ID copies, photos, a letter from a local leader, the fee).
"""

# The papers, once. A route lists the ones it needs.
PAPERS = {
    "constitution": {
        "made_by": "app",
        "en": ("Constitution (the chama's rules)",
               "The rules the members adopted, written out as a constitution, with the "
               "officials' signature lines."),
        "sw": ("Katiba (sheria za chama)",
               "Sheria ambazo wanachama walikubali, zimeandikwa kama katiba, na nafasi za "
               "sahihi za viongozi."),
    },
    "founding_minutes": {
        "made_by": "app",
        "en": ("Minutes of the founding meeting",
               "The meeting that formed the group, adopted the constitution, elected the "
               "officials and agreed to register and open a bank account."),
        "sw": ("Kumbukumbu za mkutano wa kuanzisha",
               "Mkutano ulioanzisha kikundi, ukakubali katiba, ukachagua viongozi na "
               "ukaamua kusajili na kufungua akaunti ya benki."),
    },
    "members_register": {
        "made_by": "app",
        "en": ("Register of members",
               "Every member's name, member code and role, with a column for the ID number "
               "and a signature."),
        "sw": ("Daftari la wanachama",
               "Jina la kila mwanachama, nambari ya uanachama na wadhifa, na safu ya nambari "
               "ya kitambulisho na sahihi."),
    },
    "officials_list": {
        "made_by": "app",
        "en": ("List of officials",
               "The chairperson, treasurer, secretary and other officials, for the office "
               "and the bank."),
        "sw": ("Orodha ya viongozi",
               "Mwenyekiti, mweka hazina, katibu na viongozi wengine, kwa ofisi na benki."),
    },
    "application_letter": {
        "made_by": "app",
        "en": ("Letter asking to register",
               "A short letter from the officials to the registering office, naming the "
               "group, its purpose and its members."),
        "sw": ("Barua ya kuomba usajili",
               "Barua fupi kutoka kwa viongozi kwenda ofisi ya usajili, ikitaja kikundi, "
               "lengo lake na wanachama."),
    },
    "bank_resolution": {
        "made_by": "app",
        "en": ("Resolution to open a bank account",
               "The members' decision to open the account, naming the signatories and "
               "how many must sign each withdrawal."),
        "sw": ("Azimio la kufungua akaunti ya benki",
               "Uamuzi wa wanachama kufungua akaunti, ukitaja watia sahihi na wangapi "
               "lazima watie sahihi kila utoaji wa pesa."),
    },
    "id_copies": {
        "made_by": "you",
        "en": ("Copies of national IDs",
               "A clear copy of each official's ID (some offices ask for every member's)."),
        "sw": ("Nakala za vitambulisho",
               "Nakala safi ya kitambulisho cha kila kiongozi (ofisi zingine huomba za "
               "wanachama wote)."),
    },
    "photos": {
        "made_by": "you",
        "en": ("Passport photos of the officials",
               "Usually two each, for the bank account."),
        "sw": ("Picha za pasipoti za viongozi", "Kwa kawaida mbili kila mmoja, kwa benki."),
    },
    "leader_letter": {
        "made_by": "you",
        "en": ("Letter from your local leader",
               "An introduction letter from the chief, LC1 chairperson or village/mtaa "
               "executive officer, saying the group is known in the area."),
        "sw": ("Barua ya kiongozi wa eneo",
               "Barua ya utambulisho kutoka kwa chifu, mwenyekiti wa LC1 au afisa mtendaji "
               "wa kijiji/mtaa, ikisema kikundi kinajulikana eneo hilo."),
    },
    "tax_pin": {
        "made_by": "you",
        "en": ("Tax number of the officials",
               "KRA PIN (Kenya) or TIN (Uganda, Tanzania). Banks usually ask for the "
               "signatories' numbers."),
        "sw": ("Nambari ya kodi ya viongozi",
               "KRA PIN (Kenya) au TIN (Uganda, Tanzania). Benki huomba nambari za watia "
               "sahihi."),
    },
    "fee": {
        "made_by": "you",
        "en": ("The registration fee", "Paid at the office; keep the receipt."),
        "sw": ("Ada ya usajili", "Hulipwa ofisini; weka risiti."),
    },
    "certificate": {
        "made_by": "you",
        "en": ("Your registration certificate",
               "The bank asks for it. Add its number in JiraniChama when you have it."),
        "sw": ("Cheti chako cha usajili",
               "Benki huomba. Weka nambari yake kwenye JiraniChama ukikipata."),
    },
}

_DISCLAIMER = {
    "en": "This is a guide, not legal advice. Fees and forms differ from office to office; "
          "confirm them at the office before you go.",
    "sw": "Huu ni mwongozo, si ushauri wa kisheria. Ada na fomu hutofautiana kati ya ofisi; "
          "zithibitishe ofisini kabla ya kwenda.",
}


def _route(id, papers, **text):
    return {"id": id, "papers": papers, **text}


KE = [
    _route(
        "community_group",
        ["constitution", "founding_minutes", "members_register", "officials_list",
         "application_letter", "id_copies", "leader_letter", "fee"],
        en={
            "title": "Register as a self-help (community) group",
            "office": "Sub-County Social Development Office (Department of Social "
                      "Development, State Department for Social Protection)",
            "law": "Groups are registered by the Department of Social Development. The "
                   "Community Groups Registration Act, 2022 moves registration to county "
                   "community-group registrars as it comes into force; the office will tell "
                   "you which applies in your county.",
            "who": "Most chamas: merry-go-rounds, table banking, welfare groups and small "
                   "investment groups.",
            "steps": [
                "Hold the founding meeting: adopt the constitution, elect the officials and "
                "agree to register. JiraniChama prepares the minutes and the constitution "
                "from your rules.",
                "Fill the ID numbers in the register of members and collect copies of the "
                "officials' IDs. Every member signs the register.",
                "Ask your chief or assistant chief for an introduction letter (many offices "
                "ask for it).",
                "Take the papers to the Sub-County Social Development Officer, fill the "
                "application form and pay the fee.",
                "Collect the certificate. Renew it every year, before it expires.",
                "Open the bank account (see the next guide) and add the certificate number "
                "in JiraniChama.",
            ],
            "fee": "About KES 1,000 to register and about KES 500 a year to renew. Counties "
                   "differ.",
            "time": "Often the same day, at most a few weeks.",
            "renew": "Every year.",
        },
        sw={
            "title": "Sajili kama kikundi cha kujisaidia (kikundi cha jamii)",
            "office": "Ofisi ya Maendeleo ya Jamii ya kaunti ndogo (Idara ya Maendeleo ya "
                      "Jamii, Idara ya Serikali ya Ulinzi wa Jamii)",
            "law": "Vikundi husajiliwa na Idara ya Maendeleo ya Jamii. Sheria ya Usajili wa "
                   "Vikundi vya Jamii, 2022 inahamishia usajili kwa wasajili wa kaunti "
                   "inapoanza kutumika; ofisi itakuambia ipi inatumika kaunti yako.",
            "who": "Chama nyingi: mzunguko, table banking, vikundi vya ustawi na vikundi "
                   "vidogo vya uwekezaji.",
            "steps": [
                "Fanyeni mkutano wa kuanzisha: kubalini katiba, chagueni viongozi na "
                "amueni kusajili. JiraniChama inaandaa kumbukumbu na katiba kutoka kwa "
                "sheria zenu.",
                "Jazeni nambari za vitambulisho kwenye daftari la wanachama na kusanyeni "
                "nakala za vitambulisho vya viongozi. Kila mwanachama atie sahihi.",
                "Ombeni barua ya utambulisho kutoka kwa chifu au naibu chifu (ofisi nyingi "
                "huiomba).",
                "Pelekeni karatasi kwa Afisa wa Maendeleo ya Jamii wa kaunti ndogo, jazeni "
                "fomu ya maombi na lipeni ada.",
                "Chukueni cheti. Kifanyieni upya kila mwaka, kabla hakijaisha.",
                "Fungueni akaunti ya benki (angalia mwongozo unaofuata) na wekeni nambari "
                "ya cheti kwenye JiraniChama.",
            ],
            "fee": "Takriban KES 1,000 kusajili na takriban KES 500 kwa mwaka kufanya upya. "
                   "Kaunti hutofautiana.",
            "time": "Mara nyingi siku hiyo hiyo, hadi wiki chache.",
            "renew": "Kila mwaka.",
        },
    ),
    _route(
        "bank_account",
        ["certificate", "constitution", "bank_resolution", "officials_list", "id_copies",
         "tax_pin", "photos"],
        en={
            "title": "Open the chama's bank account",
            "office": "Any bank branch (Equity, KCB, Co-op and others have group accounts)",
            "law": "Banks must know who controls an account (Proceeds of Crime and "
                   "Anti-Money Laundering Act), so every signatory is identified.",
            "who": "Every registered chama.",
            "steps": [
                "Agree at a meeting who the signatories are and that ANY TWO (or more) must "
                "sign every withdrawal. JiraniChama writes this into the resolution.",
                "Take the certificate, constitution, resolution, list of officials, and "
                "each signatory's ID, KRA PIN and photos to the branch.",
                "Ask for statements by email to the chama's JiraniChama address, so every "
                "member sees every shilling.",
                "If the bank offers an app where every signatory approves on their phone "
                "(for example a group or multi-approval account), choose it.",
            ],
            "fee": "Usually free to open; ask about monthly charges.",
            "time": "A day to a week.",
            "renew": "Tell the bank at once when officials change.",
        },
        sw={
            "title": "Fungua akaunti ya benki ya chama",
            "office": "Tawi lolote la benki (Equity, KCB, Co-op na nyingine zina akaunti za "
                      "vikundi)",
            "law": "Benki lazima zijue anayedhibiti akaunti (Sheria ya Mapato ya Uhalifu na "
                   "Kupambana na Utakatishaji wa Fedha), hivyo kila mtia sahihi hutambuliwa.",
            "who": "Kila chama kilichosajiliwa.",
            "steps": [
                "Kubalini kwenye mkutano watia sahihi ni nani na kwamba WAWILI (au zaidi) "
                "lazima watie sahihi kila utoaji. JiraniChama inaandika hili kwenye azimio.",
                "Pelekeni cheti, katiba, azimio, orodha ya viongozi, na kitambulisho, KRA "
                "PIN na picha za kila mtia sahihi kwenye tawi.",
                "Ombeni taarifa za benki zitumwe kwa barua pepe ya JiraniChama ya chama, "
                "ili kila mwanachama aone kila shilingi.",
                "Benki ikitoa programu ambapo kila mtia sahihi anaidhinisha kwa simu yake, "
                "ichagueni.",
            ],
            "fee": "Kwa kawaida bure kufungua; ulizeni kuhusu ada za kila mwezi.",
            "time": "Siku moja hadi wiki.",
            "renew": "Ijulisheni benki mara moja viongozi wakibadilika.",
        },
    ),
    _route(
        "cooperative_or_company",
        ["constitution", "founding_minutes", "members_register", "officials_list",
         "id_copies", "tax_pin"],
        en={
            "title": "Growing bigger: a co-operative (SACCO) or a company",
            "office": "County Co-operative Officer, or the Business Registration Service "
                      "(eCitizen)",
            "law": "Co-operative Societies Act (Cap. 490); Companies Act, 2015. A SACCO that "
                   "takes deposits is also licensed by SASRA.",
            "who": "Investment groups buying land or shares in the group's name, or groups "
                   "that want to take savings from the public.",
            "steps": [
                "Talk to the County Co-operative Officer or a lawyer first: these need more "
                "papers than JiraniChama prepares.",
                "A co-operative needs by-laws, at least ten members and an economic "
                "survey; a company needs a name search and directors.",
                "Bring the members' register and minutes from JiraniChama as a start.",
            ],
            "fee": "Higher than a group; ask the office.",
            "time": "Weeks to months.",
            "renew": "Yearly returns and audited accounts.",
        },
        sw={
            "title": "Kukua zaidi: chama cha ushirika (SACCO) au kampuni",
            "office": "Afisa wa Ushirika wa Kaunti, au Huduma ya Usajili wa Biashara "
                      "(eCitizen)",
            "law": "Sheria ya Vyama vya Ushirika (Sura 490); Sheria ya Makampuni, 2015. SACCO "
                   "inayochukua amana pia hupewa leseni na SASRA.",
            "who": "Vikundi vya uwekezaji vinavyonunua ardhi au hisa kwa jina la kikundi, au "
                   "vinavyotaka kuchukua akiba ya umma.",
            "steps": [
                "Ongeeni kwanza na Afisa wa Ushirika wa Kaunti au wakili: hivi vinahitaji "
                "karatasi zaidi ya zile JiraniChama inaandaa.",
                "Chama cha ushirika kinahitaji sheria ndogo, angalau wanachama kumi na "
                "utafiti wa kiuchumi; kampuni inahitaji kutafuta jina na wakurugenzi.",
                "Anzeni na daftari la wanachama na kumbukumbu kutoka JiraniChama.",
            ],
            "fee": "Juu kuliko kikundi; ulizeni ofisini.",
            "time": "Wiki hadi miezi.",
            "renew": "Ripoti za kila mwaka na hesabu zilizokaguliwa.",
        },
    ),
]

UG = [
    _route(
        "community_group",
        ["constitution", "founding_minutes", "members_register", "officials_list",
         "application_letter", "id_copies", "leader_letter", "fee"],
        en={
            "title": "Register as a community-based organisation or savings group",
            "office": "Sub-county Community Development Officer (CDO), then the District "
                      "NGO Monitoring Committee",
            "law": "The NGO Act, 2016 and NGO Regulations, 2017 (community-based "
                   "organisations are registered by the district). Groups that only save "
                   "and lend among their own members are self-help groups under the Tier 4 "
                   "Microfinance Institutions and Money Lenders Act, 2016: they are not "
                   "licensed by UMRA but are expected to register with the local government.",
            "who": "VSLAs, chamas, welfare and burial groups, small investment groups.",
            "steps": [
                "Hold the founding meeting: adopt the constitution, elect the officials and "
                "agree to register. JiraniChama prepares the minutes and constitution.",
                "Fill each member's NIN in the register of members; every member signs.",
                "Get a recommendation letter from the LC1 chairperson (and the sub-county "
                "chief if asked).",
                "Take the papers to the sub-county CDO, who forwards them to the district.",
                "Collect the district registration certificate; renew it as the district "
                "requires (often every year).",
            ],
            "fee": "Set by each district, often between UGX 20,000 and UGX 100,000.",
            "time": "A few days to a few weeks.",
            "renew": "As the district requires, often yearly.",
        },
        sw={
            "title": "Sajili kama shirika la kijamii au kikundi cha akiba",
            "office": "Afisa wa Maendeleo ya Jamii wa kaunti ndogo (CDO), kisha Kamati ya "
                      "Ufuatiliaji wa NGO ya Wilaya",
            "law": "Sheria ya NGO, 2016 na Kanuni za NGO, 2017 (mashirika ya kijamii "
                   "husajiliwa na wilaya). Vikundi vinavyoweka akiba na kukopeshana wenyewe "
                   "tu ni vikundi vya kujisaidia chini ya Sheria ya Taasisi za Daraja la 4 "
                   "na Wakopeshaji, 2016: havipewi leseni na UMRA lakini vinatarajiwa "
                   "kusajiliwa na serikali ya mtaa.",
            "who": "VSLA, chama, vikundi vya ustawi na mazishi, vikundi vidogo vya uwekezaji.",
            "steps": [
                "Fanyeni mkutano wa kuanzisha: kubalini katiba, chagueni viongozi na amueni "
                "kusajili. JiraniChama inaandaa kumbukumbu na katiba.",
                "Jazeni NIN ya kila mwanachama kwenye daftari; kila mwanachama atie sahihi.",
                "Pateni barua ya mapendekezo kutoka kwa mwenyekiti wa LC1 (na chifu wa "
                "kaunti ndogo akiomba).",
                "Pelekeni karatasi kwa CDO wa kaunti ndogo, ambaye huzipeleka wilayani.",
                "Chukueni cheti cha wilaya; kifanyieni upya kama wilaya inavyotaka (mara "
                "nyingi kila mwaka).",
            ],
            "fee": "Huwekwa na kila wilaya, mara nyingi kati ya UGX 20,000 na UGX 100,000.",
            "time": "Siku chache hadi wiki chache.",
            "renew": "Kama wilaya inavyotaka, mara nyingi kila mwaka.",
        },
    ),
    _route(
        "bank_account",
        ["certificate", "constitution", "bank_resolution", "officials_list", "id_copies",
         "tax_pin", "photos"],
        en={
            "title": "Open the group's bank account",
            "office": "Any bank branch (Centenary, Stanbic, dfcu and others have group "
                      "accounts)",
            "law": "Anti-Money Laundering Act, 2013: the bank identifies every signatory.",
            "who": "Every registered group.",
            "steps": [
                "Agree who the signatories are and that ANY TWO (or more) must sign each "
                "withdrawal; JiraniChama writes it into the resolution.",
                "Take the certificate, constitution, resolution, officials' list, and each "
                "signatory's national ID, TIN and photos to the branch.",
                "Ask for statements by email to the group's JiraniChama address.",
            ],
            "fee": "Usually free to open; ask about monthly charges.",
            "time": "A day to a week.",
            "renew": "Tell the bank at once when officials change.",
        },
        sw={
            "title": "Fungua akaunti ya benki ya kikundi",
            "office": "Tawi lolote la benki (Centenary, Stanbic, dfcu na nyingine)",
            "law": "Sheria ya Kupambana na Utakatishaji wa Fedha, 2013: benki humtambua kila "
                   "mtia sahihi.",
            "who": "Kila kikundi kilichosajiliwa.",
            "steps": [
                "Kubalini watia sahihi ni nani na kwamba WAWILI (au zaidi) lazima watie "
                "sahihi kila utoaji; JiraniChama inaandika hili kwenye azimio.",
                "Pelekeni cheti, katiba, azimio, orodha ya viongozi, na kitambulisho, TIN na "
                "picha za kila mtia sahihi tawini.",
                "Ombeni taarifa za benki zitumwe kwa anwani ya JiraniChama ya kikundi.",
            ],
            "fee": "Kwa kawaida bure kufungua; ulizeni kuhusu ada za kila mwezi.",
            "time": "Siku moja hadi wiki.",
            "renew": "Ijulisheni benki mara moja viongozi wakibadilika.",
        },
    ),
    _route(
        "cooperative_or_company",
        ["constitution", "founding_minutes", "members_register", "officials_list",
         "id_copies", "tax_pin"],
        en={
            "title": "Growing bigger: a SACCO or a company",
            "office": "Registrar of Cooperatives (Ministry of Trade), or URSB for a company",
            "law": "Cooperative Societies Act; a deposit-taking SACCO is also licensed by "
                   "UMRA under the Tier 4 Act, 2016.",
            "who": "Groups taking savings from non-members, or owning land and shares.",
            "steps": [
                "Talk to the district commercial officer or a lawyer first.",
                "Bring the members' register and minutes from JiraniChama as a start.",
            ],
            "fee": "Ask the office.",
            "time": "Weeks to months.",
            "renew": "Yearly returns.",
        },
        sw={
            "title": "Kukua zaidi: SACCO au kampuni",
            "office": "Msajili wa Vyama vya Ushirika (Wizara ya Biashara), au URSB kwa kampuni",
            "law": "Sheria ya Vyama vya Ushirika; SACCO inayochukua amana pia hupewa leseni na "
                   "UMRA chini ya Sheria ya Daraja la 4, 2016.",
            "who": "Vikundi vinavyochukua akiba ya wasio wanachama, au vinavyomiliki ardhi na "
                   "hisa.",
            "steps": [
                "Ongeeni kwanza na afisa wa biashara wa wilaya au wakili.",
                "Anzeni na daftari la wanachama na kumbukumbu kutoka JiraniChama.",
            ],
            "fee": "Ulizeni ofisini.",
            "time": "Wiki hadi miezi.",
            "renew": "Ripoti za kila mwaka.",
        },
    ),
]

TZ = [
    _route(
        "community_group",
        ["constitution", "founding_minutes", "members_register", "officials_list",
         "application_letter", "id_copies", "leader_letter", "fee"],
        en={
            "title": "Register as a community microfinance group (VICOBA, VSLA, chama)",
            "office": "The council (Halmashauri), through the Community Development Officer "
                      "of your ward or council",
            "law": "The Microfinance Act, 2018 and the Microfinance (Community Microfinance "
                   "Groups) Regulations, 2019: groups that save and lend to their members "
                   "must register with the local government authority. The Bank of Tanzania "
                   "oversees the system.",
            "who": "VICOBA, VSLAs, chamas and other groups that save and lend.",
            "steps": [
                "Hold the founding meeting: adopt the constitution, elect the officials and "
                "agree to register. JiraniChama prepares the minutes and constitution.",
                "Fill each member's NIDA number in the register; every member signs.",
                "Get an introduction letter from the village or mtaa executive officer.",
                "Take the papers to the Community Development Officer, fill the council's "
                "form and pay the fee.",
                "Collect the registration certificate and keep it with the group's records.",
            ],
            "fee": "Set by each council; ask the Community Development Officer.",
            "time": "A few days to a few weeks.",
            "renew": "Ask the council when the certificate must be renewed.",
        },
        sw={
            "title": "Sajili kama kikundi cha huduma ndogo za fedha cha jamii (VICOBA, VSLA, "
                     "chama)",
            "office": "Halmashauri, kupitia Afisa Maendeleo ya Jamii wa kata au halmashauri "
                      "yako",
            "law": "Sheria ya Huduma Ndogo za Fedha, 2018 na Kanuni za Vikundi vya Huduma "
                   "Ndogo za Fedha vya Jamii, 2019: vikundi vinavyoweka akiba na kukopesha "
                   "wanachama wake lazima visajiliwe na mamlaka ya serikali za mitaa. Benki "
                   "Kuu ya Tanzania inasimamia mfumo.",
            "who": "VICOBA, VSLA, chama na vikundi vingine vinavyoweka akiba na kukopesha.",
            "steps": [
                "Fanyeni mkutano wa kuanzisha: kubalini katiba, chagueni viongozi na amueni "
                "kusajili. JiraniChama inaandaa kumbukumbu na katiba.",
                "Jazeni nambari ya NIDA ya kila mwanachama kwenye daftari; kila mwanachama "
                "atie sahihi.",
                "Pateni barua ya utambulisho kutoka kwa afisa mtendaji wa kijiji au mtaa.",
                "Pelekeni karatasi kwa Afisa Maendeleo ya Jamii, jazeni fomu ya halmashauri "
                "na lipeni ada.",
                "Chukueni cheti cha usajili na mkitunze pamoja na kumbukumbu za kikundi.",
            ],
            "fee": "Huwekwa na kila halmashauri; mulizeni Afisa Maendeleo ya Jamii.",
            "time": "Siku chache hadi wiki chache.",
            "renew": "Ulizeni halmashauri cheti kinafanywa upya lini.",
        },
    ),
    _route(
        "bank_account",
        ["certificate", "constitution", "bank_resolution", "officials_list", "id_copies",
         "tax_pin", "photos"],
        en={
            "title": "Open the group's bank account",
            "office": "Any bank branch (CRDB, NMB, NBC and others have group accounts)",
            "law": "Anti-Money Laundering Act: the bank identifies every signatory.",
            "who": "Every registered group.",
            "steps": [
                "Agree who the signatories are and that ANY TWO (or more) must sign each "
                "withdrawal; JiraniChama writes it into the resolution.",
                "Take the certificate, constitution, resolution, officials' list, and each "
                "signatory's NIDA ID, TIN and photos to the branch.",
                "Ask for statements by email to the group's JiraniChama address.",
            ],
            "fee": "Usually free to open; ask about monthly charges.",
            "time": "A day to a week.",
            "renew": "Tell the bank at once when officials change.",
        },
        sw={
            "title": "Fungua akaunti ya benki ya kikundi",
            "office": "Tawi lolote la benki (CRDB, NMB, NBC na nyingine)",
            "law": "Sheria ya Kupambana na Utakatishaji wa Fedha: benki humtambua kila mtia "
                   "sahihi.",
            "who": "Kila kikundi kilichosajiliwa.",
            "steps": [
                "Kubalini watia sahihi ni nani na kwamba WAWILI (au zaidi) lazima watie "
                "sahihi kila utoaji; JiraniChama inaandika hili kwenye azimio.",
                "Pelekeni cheti, katiba, azimio, orodha ya viongozi, na kitambulisho cha "
                "NIDA, TIN na picha za kila mtia sahihi tawini.",
                "Ombeni taarifa za benki zitumwe kwa anwani ya JiraniChama ya kikundi.",
            ],
            "fee": "Kwa kawaida bure kufungua; ulizeni kuhusu ada za kila mwezi.",
            "time": "Siku moja hadi wiki.",
            "renew": "Ijulisheni benki mara moja viongozi wakibadilika.",
        },
    ),
    _route(
        "cooperative_or_company",
        ["constitution", "founding_minutes", "members_register", "officials_list",
         "id_copies", "tax_pin"],
        en={
            "title": "Growing bigger: a SACCOS or a company",
            "office": "Tanzania Cooperative Development Commission (TCDC), or BRELA for a "
                      "company",
            "law": "Cooperative Societies Act, 2013; a SACCOS is also covered by the "
                   "Microfinance Act, 2018.",
            "who": "Groups taking savings from non-members, or owning land and shares.",
            "steps": [
                "Talk to the district cooperative officer or a lawyer first.",
                "Bring the members' register and minutes from JiraniChama as a start.",
            ],
            "fee": "Ask the office.",
            "time": "Weeks to months.",
            "renew": "Yearly returns.",
        },
        sw={
            "title": "Kukua zaidi: SACCOS au kampuni",
            "office": "Tume ya Maendeleo ya Ushirika (TCDC), au BRELA kwa kampuni",
            "law": "Sheria ya Vyama vya Ushirika, 2013; SACCOS pia iko chini ya Sheria ya "
                   "Huduma Ndogo za Fedha, 2018.",
            "who": "Vikundi vinavyochukua akiba ya wasio wanachama, au vinavyomiliki ardhi na "
                   "hisa.",
            "steps": [
                "Ongeeni kwanza na afisa ushirika wa wilaya au wakili.",
                "Anzeni na daftari la wanachama na kumbukumbu kutoka JiraniChama.",
            ],
            "fee": "Ulizeni ofisini.",
            "time": "Wiki hadi miezi.",
            "renew": "Ripoti za kila mwaka.",
        },
    ),
]


def _short(office, law, steps_en):
    """A shorter guide for a country JiraniChama has not opened yet."""
    papers = ["constitution", "founding_minutes", "members_register", "officials_list",
              "application_letter", "id_copies", "leader_letter", "fee"]
    text = {"title": "Register your group", "office": office, "law": law,
            "who": "Savings, welfare and investment groups.", "steps": steps_en,
            "fee": "Ask the office.", "time": "Ask the office.", "renew": "Ask the office."}
    return [_route("community_group", papers, en=text, sw=text)]


_COMMON_STEPS = [
    "Hold the founding meeting and adopt the constitution; JiraniChama prepares both.",
    "Fill each member's ID number in the register of members; every member signs.",
    "Get a letter from your local leader.",
    "Take the papers to the office below and ask for the group registration form.",
]

OTHERS = {
    "RW": _short("Your sector office (savings groups), or the Rwanda Cooperative Agency "
                 "for a cooperative",
                 "Savings groups are recognised at sector level; cooperatives are "
                 "registered by the Rwanda Cooperative Agency under the law on cooperatives.",
                 _COMMON_STEPS),
    "BI": _short("Your commune office, or the Ministry of the Interior for a non-profit "
                 "association",
                 "Non-profit associations are registered under the law on non-profit "
                 "organisations (2017).", _COMMON_STEPS),
    "CD": _short("Your commune or territory office, or the provincial Ministry of Justice "
                 "for a non-profit association (ASBL)",
                 "Non-profit associations (ASBL) are governed by Law No. 004/2001.",
                 _COMMON_STEPS),
    "SS": _short("The Relief and Rehabilitation Commission (RRC) state office",
                 "Community-based organisations register under the NGO Act, 2016.",
                 _COMMON_STEPS),
    "ET": _short("Your woreda office (equb and iddir), or the cooperative agency for a "
                 "savings and credit cooperative",
                 "Savings and credit cooperatives are registered under the Cooperative "
                 "Societies Proclamation No. 985/2016.", _COMMON_STEPS),
    "SO": _short("Your district (degmo) office or the regional Ministry of Interior",
                 "Community groups register with the local administration.",
                 _COMMON_STEPS),
}

GUIDES = {"KE": KE, "UG": UG, "TZ": TZ, **OTHERS}


def guide(country: str, lang: str) -> dict:
    """The guide for a country in a language (English when there is no translation)."""
    lang = lang if lang in ("en", "sw") else "en"
    routes = GUIDES.get(country) or GUIDES["KE"]
    return {
        "country": country if country in GUIDES else "KE",
        "detailed": country in ("KE", "UG", "TZ"),
        "disclaimer": _DISCLAIMER[lang],
        "routes": [{"id": r["id"], "papers": r["papers"], **r[lang]} for r in routes],
        "papers": {k: {"made_by": p["made_by"], "title": p[lang][0], "what": p[lang][1]}
                   for k, p in PAPERS.items()},
    }


# The papers JiraniChama fills in, in the order a group usually needs them.
MADE_BY_APP = [k for k, p in PAPERS.items() if p["made_by"] == "app"]
