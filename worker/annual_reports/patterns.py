"""Regex vocabulary for pulling forward-looking content out of annual reports.

Kept in its own module so the patterns can be tuned and unit-tested without
touching the download/extract machinery.

The hard problem here is not finding guidance, it is rejecting everything else.
A large annual report is mostly financial statements, BRSR/ESG tables, CSR
annexures and governance boilerplate, all of which are dense with numbers and
will happily match a naive "revenue grew" pattern. Hence three layers of
defence: page-level exclusion, paragraph-level boilerplate rejection, and a
prose test that throws out anything shaped like a table.
"""
import re

C = re.IGNORECASE

CATEGORIES = {
    "future_plans": re.compile(r"""(
        plan(?:s|ned|ning)?\s+to\s+(?:set\s?up|expand|invest|add|enter|launch|acquire|commission|foray|scale|build|increase)
      | (?:capex|capital\s+expenditure|capital\s+outlay)\s+(?:of|plan|programme|program|towards)
      | expansion\s+(?:plan|project|programme|of\s+capacity|is\s+underway)
      | (?:greenfield|brownfield)\s+(?:project|expansion|facility|plant)
      | new\s+(?:plant|facility|manufacturing\s+(?:unit|facility|line))
      | (?:commission|operational|complet)\w*\s+(?:by|in|during)\s+(?:FY\s?\d{2}|Q[1-4]|20[2-4]\d)
      | under\s+(?:construction|implementation|commissioning)
      | (?:foray|entry)\s+into | diversif\w+\s+into
      | (?:strategic\s+priorit|road\s?map|way\s+forward|growth\s+strategy)
      | (?:proposed|planned)\s+(?:acquisition|merger|expansion|investment|capacity)
      | (?:will|shall)\s+(?:be\s+)?(?:set\s?up|commission|expand|invest|add|launch)
    )""", C | re.X),

    "kpis": re.compile(r"""(
        order\s+book\s+(?:of|stood|position|stands|at)
      | (?:installed\s+)?capacity\s+(?:of\s+\d|utilis\w+|utiliz\w+|stands|stood|increased|expanded|rose)
      | \d[\d,.]*\s*(?:MTPA|TPA|MW|GW|MMSCMD|TCD|KL/day|tonnes\s+per\s+annum)
      | (?:EBITDA|operating|gross|net|PAT|contribution)\s+margin\s+(?:of|at|stood|improved|expanded|declined|was)
      | (?:ROCE|RoCE|ROE|RoE|RoNW)\s+(?:of|at|stood|improved|was)
      | market\s+share\s+(?:of|stood|stands|increased|improved)
      | (?:revenue|turnover|volume|sales|profit|topline)\w*\s+(?:grew|increased|rose|declined|degrew|expanded)\s+
      | same[\s-]store\s+sales | realisation\s+per\s | average\s+selling\s+price
      | (?:AUM|assets\s+under\s+management)\s+(?:of|at|stood|grew)
    )""", C | re.X),

    "guidance": re.compile(r"""(
        we\s+(?:expect|anticipate|aim|envisage|foresee|project|target)
      | (?:the\s+)?compan(?:y|ies)\s+(?:expects|anticipates|aims|envisages|targets|is\s+confident)
      | (?:targeting|target\s+of|targets?\s+to|aspire\w*\s+to)
      | (?:revenue|growth|margin|volume)\s+guidance | guidance\s+(?:of|for)
      | outlook\s+for\s+(?:FY|the\s+(?:year|coming))
      | going\s+forward | in\s+the\s+(?:coming|next|ensuing)\s+(?:year|years|fiscal|quarter)
      | (?:by|before)\s+(?:FY\s?\d{2}|20[2-4]\d)\b
      | expected\s+to\s+(?:grow|reach|increase|double|treble|improve|be\s+completed|contribute|commence)
      | (?:confident|on\s+track|poised|well\s+placed)\s+(?:of|to)
      | (?:should|will)\s+(?:drive|deliver|improve|enhance|support)\s+(?:growth|margins?|revenue|profitability)
    )""", C | re.X),
}

# Paragraph-level rejects: statutory, accounting and ESG/CSR boilerplate.
BOILERPLATE = re.compile(r"""(
    forward[\s-]?looking\s+statements | actual\s+results\s+(?:may|could|might)\s+differ
  | (?:the\s+)?plan\s+guarantees | defined\s+(?:benefit|contribution)\s+plan
  | provident\s+fund | gratuity | actuarial\s+(?:valuation|assumption|gain|loss)
  | vigil\s+mechanism | whistle[\s-]?blower | risk\s+of\s+fraud
  | pursuant\s+to\s+(?:section|regulation|rule|clause)
  | in\s+accordance\s+with\s+(?:the\s+)?(?:companies\s+act|ind\s+as|schedule|provisions)
  | as\s+(?:per|required\s+by)\s+the\s+(?:requirement|provision|companies\s+act)
  | sitting\s+fees | managerial\s+remuneration | remuneration\s+of\s+(?:the\s+)?director
  | unclaimed\s+(?:dividend|shares) | investor\s+education\s+and\s+protection
  | secretarial\s+audit | cost\s+auditor | statutory\s+auditor
  | related\s+party\s+transactions?\s+(?:policy|entered)
  | e[\s-]?voting | book\s+closure | record\s+date | this\s+certificate\s+is\s+issued
  | deferred\s+tax\s+(?:asset|liabilit) | lease\s+liabilit | right[\s-]of[\s-]use
  # --- CSR / BRSR / ESG statutory tables ---
  | section\s+135 | corporate\s+social\s+responsibilit | CSR\s+(?:project|committee|policy|amount|spent)
  | (?:LTIFR|POSH|sexual\s+harassment) | gender\s+diversity | employee\s+(?:retention|well[\s-]?being)
  | grievance\s+redressal | stakeholder\s+engagement | materiality\s+assessment
  | scope\s+[123]\s+emission | GHG\s+emission | energy\s+(?:consumed|intensity)
  | water\s+(?:withdrawal|consumption|discharge) | waste\s+(?:generated|recovered|management)
  | number\s+of\s+(?:locations|complaints|training) | states\s+and\s+\d+\s+union\s+territories
  | details\s+of\s+business\s+activities | %\s+of\s+turnover\s+of\s+the\s+entity
  | training\s+and\s+awareness | permanent\s+and\s+other\s+than\s+permanent
  # --- committee / board-bio prose that mimics strategy language ---
  | nomination\s+and\s+remuneration\s+committee | audit\s+committee\s+(?:met|comprises|consists)
  | internal\s+auditors? | risk\s+management\s+committee\s+(?:met|comprises)
  | stakeholders?\s+relationship\s+committee | independent\s+directors?\s+(?:met|meeting)
  | (?:he|she)\s+(?:led|joined|holds|serves|has\s+been)\s+the\s+(?:company|board|group)
  | (?:B\.?Tech|MBA|chartered\s+accountant)\s+(?:from|degree)
  # --- AGM-notice language that mimics guidance ---
  # "the Company anticipates entering into transactions with related parties"
  # matches the guidance vocabulary exactly but is a resolution, not an outlook.
  | transactions?\s+with\s+related\s+part(?:y|ies)
  | materiality\s+threshold | resolutions?\s+nos?\.
  | your\s+approval\s+is\s+being\s+sought | set\s+out\s+in\s+(?:the\s+)?notice
  | not\s+applicable\s+since | projects?\s+under\s+implementation\s+accounts
  | disclosure\s+related\s+to\s+project\s+finance
)""", C | re.X)

# Whole-page rejects: financial statements, notices, governance and BRSR/CSR.
EXCLUDE_PAGE = re.compile(r"""(
    independent\s+auditor'?s?\s+report
  | notes?\s+(?:to|forming\s+part\s+of)\s+the\s+(?:standalone|consolidated|financial)
  | balance\s+sheet\s+as\s+at | statement\s+of\s+profit\s+and\s+loss\s+for
  | cash\s+flow\s+statement | statement\s+of\s+changes\s+in\s+equity
  | notice\s+(?:of|is\s+hereby\s+given).{0,60}annual\s+general\s+meeting
  | report\s+on\s+corporate\s+governance | (?:form\s+)?(?:AOC|MGT|MR)[\s-]?\d
  | business\s+responsibility\s+and\s+sustainability\s+report
  | annexure.{0,40}(?:CSR|corporate\s+social|secretarial|conservation\s+of\s+energy)
  | attendance\s+slip | proxy\s+form | route\s+map
)""", C | re.X)

# A candidate must carry a figure, a period or a percentage.
SUBSTANCE = re.compile(
    r"(\d[\d,]*\.?\d*\s*(?:%|per\s?cent|crore|lakh|million|billion|bn|mn|MW|GW|MTPA|TPA|tonnes)"
    r"|FY\s?\d{2}|20[2-4]\d|Q[1-4]\s?FY?\s?\d{2}|₹|Rs\.?\s?\d)", C)

_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]{2,}$")
_NUMLIKE = re.compile(r"[\(\)₹`Rs.,\-]*\d[\d,.\)%\-]*$")


def numeric_density(text):
    """Share of whitespace tokens that look numeric. Financial tables run high."""
    toks = text.split()
    if len(toks) < 20:
        return 1.0
    return sum(1 for t in toks if _NUMLIKE.match(t)) / len(toks)


def is_prose(text):
    """Reject table rows and headline fragments; keep management commentary.

    Tables shred into short capitalised fragments and numbers, so they show a
    low share of ordinary lowercase words and carry almost no sentence
    punctuation. Real commentary is the opposite.
    """
    toks = text.split()
    if len(toks) < 15:
        return False
    words = [t for t in toks if _WORD.match(t)]
    if len(words) / len(toks) < 0.62:
        return False
    if sum(1 for w in words if w[0].islower()) / max(len(words), 1) < 0.55:
        return False
    return text.count(".") + text.count(",") >= 2


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u201c\"'\u20b9])")
# Layout artefacts: bullet glyphs, repeated dots, stray pipes from table borders.
_CLEAN = re.compile(r"[\u2022\u25cf\u25aa\uf0b7|]+|\.{3,}")


_HYPHEN_WRAP = re.compile(r"(\w)-\s+(\w)")


def dehyphenate(text):
    """Rejoin words split across a PDF line or column break.

    Column typesetting leaves artefacts like "data- driven" and "well-
    positioned". These are legitimate words, not garbled text, so they are
    repaired rather than filtered -- an earlier pass that treated them as
    corruption would have discarded 7% of otherwise good extracts.
    """
    return _HYPHEN_WRAP.sub(r"-", text)


def sentences(page_text):
    """Split a page into sentences, robust to magazine-style PDF layouts.

    Annual reports are typeset in columns and callout boxes, so blank lines are
    not paragraph boundaries -- a page often extracts as one long run of short
    lines. Normalising to a single stream and splitting on sentence punctuation
    is far more reliable than splitting on newlines.
    """
    flat = dehyphenate(" ".join(_CLEAN.sub(" ", page_text).split()))
    return [s.strip() for s in _SENT_SPLIT.split(flat) if s.strip()]


def windows(page_text, max_chars=700):
    """Yield (index, sentence, context) so a hit can be shown with its neighbour."""
    sents = sentences(page_text)
    for i, s in enumerate(sents):
        if not (40 <= len(s) <= max_chars):
            continue
        ctx = s
        if len(ctx) < 260 and i + 1 < len(sents):
            nxt = sents[i + 1]
            if len(ctx) + len(nxt) + 1 <= max_chars:
                ctx = f"{ctx} {nxt}"
        yield i, s, ctx
