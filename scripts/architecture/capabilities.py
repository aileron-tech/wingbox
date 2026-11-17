"""What a file does that a layer may or may not do: capabilities found by syntax, and their rules.

A capability is seen in the source without running it, line by line, with the
line as evidence:

  sql            a string literal that starts a SQL statement passed to a call
                 (execute, query, raw, ...), a query-builder chain, a session or
                 entity-manager query, a Prisma model call
  db-client      an import of a database driver or ORM client
  http-client    outbound HTTP (requests, httpx, fetch, axios, HttpClient, ...)
  framework      an import of a web framework
  http-response  building an HTTP response (JSONResponse, res.status(), ResponseEntity, ...)
  env            reading the environment
  filesystem     file I/O
  process        starting a process

A SQL statement in a string literal that is not passed to a query call on the
same line (built in a variable and run elsewhere) is only suspected: it is a
candidate, never a violation. Comment lines are skipped. SQL assembled at run
time or hidden behind a helper of another package is outside what this sees.

Each style states where a capability is allowed (`allowed_in`) or not
(`forbidden_in`), by layer; a part of .wingbox/config.yaml can restate it.
"""
from __future__ import annotations

import bisect
import functools
import re
from dataclasses import dataclass

CAPABILITIES = ("sql", "db-client", "http-client", "framework", "http-response", "env", "filesystem", "process")
EVIDENCE_LENGTH = 100


@dataclass(frozen=True)
class Finding:
    capability: str
    line: int
    evidence: str     # the source line, trimmed
    suspect: bool = False   # seen but not proven (a SQL literal not passed to a query call)


@dataclass(frozen=True)
class CapabilityRule:
    capability: str
    allowed_in: tuple[str, ...] = ()      # empty: no allowlist
    forbidden_in: tuple[str, ...] = ()
    fix: str = ""

    def breaks(self, layer: str) -> bool:
        if self.allowed_in and layer not in self.allowed_in:
            return True
        return layer in self.forbidden_in


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------

_PY = ("py",)
_JS = ("ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts")
_PHP = ("php",)
_RB = ("rb",)
_JAVA = ("java", "kt")
_CS = ("cs",)
_GO = ("go",)

_COMMENT = {
    **{ext: ("#",) for ext in _PY + _RB},
    **{ext: ("//", "/*", "*") for ext in _JS + _JAVA + _CS + _GO},
    **{ext: ("//", "#", "/*", "*") for ext in _PHP},
}

_SQL_TEXT = (
    r"""(?:^|[\s"'`(])(?:SELECT\s+[\w*"`\[(].*?\bFROM\b|INSERT\s+INTO\s|UPDATE\s+[\w."`\[\]]+\s+SET\s|DELETE\s+FROM\s"""
    r"""|CREATE\s+(?:TABLE|INDEX|VIEW)\s|ALTER\s+TABLE\s|DROP\s+(?:TABLE|INDEX|VIEW)\s|TRUNCATE\s+TABLE\s|MERGE\s+INTO\s)"""
)
_SQL_START = re.compile(_SQL_TEXT, re.IGNORECASE)
# Without a query call in sight, only upper-case keywords are taken for SQL: "select an option from" is prose.
_SQL_START_UPPER = re.compile(_SQL_TEXT)
# A string literal on the line: the SQL must sit inside one.
_LITERAL = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`|\"\"\"|''')""")
_SQL_CALL = re.compile(
    r"""(?:\.|\b)(?:execute|executemany|executescript|exec|query|queryRaw|executeRaw|\$queryRaw|\$executeRaw"""
    r"""|\$queryRawUnsafe|\$executeRawUnsafe|raw|unsafe|prepare|prepareStatement|createQuery|createNativeQuery"""
    r"""|query_row|QueryRow|QueryRowContext|QueryContext|ExecContext|Query|Exec|select_all|select_one|find_by_sql"""
    r"""|FromSqlRaw|FromSqlInterpolated|ExecuteSqlRaw|ExecuteSqlInterpolated|SqlCommand|text|sql|statement|unprepared"""
    r"""|selectRaw|whereRaw|orderByRaw|havingRaw|DB::select|DB::insert|DB::update|DB::delete)\s*(?:<[^>]*>)?\s*[(`]""",
)

# Query APIs that are SQL whatever their arguments are.
_SQL_API: dict[tuple[str, ...], re.Pattern[str]] = {
    _PY: re.compile(r"""\b(?:session|db\.session|Session\(\))\.(?:query|execute)\(|\bcursor\(\)\.execute\(|\.cursor\.execute\("""),
    _JS: re.compile(
        r"""\bprisma\.\w+\.(?:findMany|findUnique|findFirst|findUniqueOrThrow|findFirstOrThrow|create|createMany|update"""
        r"""|updateMany|upsert|delete|deleteMany|count|aggregate|groupBy)\(|\.createQueryBuilder\(|\bknex\(\s*['"`]"""
        r"""|\b(?:db|tx|trx)\.(?:select|selectDistinct|insert|update|delete)\((?!\s*['"`])|\.(?:selectFrom|insertInto|updateTable|deleteFrom)\("""
        r"""|\bsql`"""
    ),
    _PHP: re.compile(r"""\bDB::(?:select|insert|update|delete|statement|table|raw|unprepared|selectOne)\(|->(?:prepare|exec)\("""),
    _RB: re.compile(r"""\.find_by_sql\(|\bconnection\.(?:execute|exec_query|select_all|select_one|select_value)\(|\bArel\.sql\("""),
    _JAVA: re.compile(r"""\.(?:createQuery|createNativeQuery|prepareStatement)\(|\bjdbcTemplate\.\w+\(|@Query\("""),
    _CS: re.compile(r"""\.(?:FromSqlRaw|FromSqlInterpolated|ExecuteSqlRaw|ExecuteSqlRawAsync|ExecuteSqlInterpolated)\(|\bnew\s+SqlCommand\("""),
    _GO: re.compile(r"""\b(?:db|tx|conn|pool)\.(?:Query|QueryRow|Exec|QueryContext|QueryRowContext|ExecContext)\("""),
}

# Imports (and a few constructors) per capability.
_IMPORTS: dict[str, dict[tuple[str, ...], re.Pattern[str]]] = {
    "db-client": {
        _PY: re.compile(
            r"""^[ \t]*(?:from|import)\s+(?:psycopg2?|asyncpg|sqlalchemy|sqlmodel|pymysql|MySQLdb|sqlite3|aiosqlite|pymongo|motor"""
            r"""|redis|peewee|tortoise|databases|django\.db|cassandra|elasticsearch)\b"""
        ),
        _JS: re.compile(
            r"""(?:from\s+|require\(\s*|import\(\s*)['"](?:pg|pg-promise|postgres|mysql2?|sqlite3|better-sqlite3|@prisma/client|knex"""
            r"""|drizzle-orm(?:/[\w-]+)*|typeorm|sequelize|mongoose|mongodb|ioredis|redis|kysely|@mikro-orm/[\w-]+|@libsql/client"""
            r"""|@neondatabase/serverless|@planetscale/database)['"]"""
        ),
        _PHP: re.compile(r"""^[ \t]*use\s+(?:Illuminate\\Support\\Facades\\DB|Illuminate\\Database\\|Doctrine\\(?:ORM|DBAL)\\)|\bnew\s+\\?PDO\("""),
        _RB: re.compile(r"""^[ \t]*require\s+['"](?:pg|mysql2|sqlite3|sequel|mongo|redis|active_record)['"]|<\s*ActiveRecord::Base\b"""),
        _JAVA: re.compile(
            r"""^[ \t]*import\s+(?:java\.sql\.|javax\.persistence\.|jakarta\.persistence\.|org\.hibernate\.|org\.springframework\.jdbc\."""
            r"""|org\.springframework\.data\.jpa\.|org\.jooq\.|com\.mongodb\.)"""
        ),
        _CS: re.compile(
            r"""^[ \t]*using\s+(?:Microsoft\.EntityFrameworkCore|System\.Data\.SqlClient|Microsoft\.Data\.SqlClient|Dapper|Npgsql"""
            r"""|MySql\.Data|MongoDB\.Driver)\b"""
        ),
        _GO: re.compile(
            r"""^[ \t]*(?:import\s+)?(?:\w+\s+)?"(?:database/sql|gorm\.io/gorm|github\.com/jmoiron/sqlx|github\.com/jackc/pgx[\w/]*"""
            r"""|go\.mongodb\.org/mongo-driver[\w/]*|github\.com/go-sql-driver/mysql|github\.com/lib/pq)\""""
        ),
    },
    "http-client": {
        _PY: re.compile(r"""^[ \t]*(?:from|import)\s+(?:requests|httpx|aiohttp|urllib\.request|urllib3|httplib2)\b"""),
        _JS: re.compile(
            r"""(?:from\s+|require\(\s*)['"](?:axios|node-fetch|got|undici|ky|superagent|@nestjs/axios|cross-fetch|isomorphic-fetch)['"]"""
            r"""|(?<![\w.])fetch\(\s*[`'"]?(?:https?:|\$\{|[A-Za-z_])"""
        ),
        _PHP: re.compile(r"""^[ \t]*use\s+(?:Illuminate\\Support\\Facades\\Http|GuzzleHttp\\)|\bcurl_init\(|\bHttp::(?:get|post|put|patch|delete|withHeaders)\("""),
        _RB: re.compile(r"""\bNet::HTTP\b|\bFaraday\.|\bHTTParty\.|\bRestClient\.|^[ \t]*require\s+['"](?:net/http|faraday|httparty|rest-client)['"]"""),
        _JAVA: re.compile(
            r"""^[ \t]*import\s+(?:java\.net\.http\.|org\.springframework\.web\.client\.|org\.springframework\.web\.reactive\.function\.client\."""
            r"""|okhttp3\.|feign\.|org\.apache\.http(?:client5?)?\.)"""
        ),
        _CS: re.compile(r"""\bnew\s+HttpClient\(|\bIHttpClientFactory\b|^[ \t]*using\s+(?:RestSharp|Refit)\b"""),
        _GO: re.compile(r"""\bhttp\.(?:Get|Post|PostForm|Head|NewRequest|NewRequestWithContext)\(|"github\.com/go-resty/resty[\w/]*\""""),
    },
    "framework": {
        _PY: re.compile(
            r"""^[ \t]*(?:from|import)\s+(?:fastapi|flask|starlette|django\.(?:http|views|shortcuts|urls)|rest_framework|aiohttp\.web"""
            r"""|tornado\.web|sanic|litestar)\b"""
        ),
        _JS: re.compile(
            r"""(?:from\s+|require\(\s*)['"](?:express|fastify|koa|@koa/router|hono|@nestjs/(?:common|core|platform-express"""
            r"""|platform-fastify)|next/server|@hapi/hapi|restify)['"]"""
        ),
        _PHP: re.compile(r"""^[ \t]*use\s+(?:Illuminate\\Http\\|Illuminate\\Routing\\|Symfony\\Component\\HttpFoundation\\|Symfony\\Component\\Routing\\)"""),
        _RB: re.compile(r"""<\s*(?:ApplicationController|ActionController::(?:Base|API))\b|\bSinatra::Base\b"""),
        _JAVA: re.compile(r"""^[ \t]*import\s+(?:org\.springframework\.web\.bind\.|org\.springframework\.http\.|javax\.ws\.rs\.|jakarta\.ws\.rs\.|io\.micronaut\.http\.)"""),
        _CS: re.compile(r"""^[ \t]*using\s+Microsoft\.AspNetCore\.(?:Mvc|Http|Routing)\b"""),
        _GO: re.compile(
            r""""github\.com/(?:gin-gonic/gin|labstack/echo[\w/]*|gofiber/fiber[\w/]*|go-chi/chi[\w/]*|gorilla/mux)"|\bhttp\.(?:HandleFunc|ResponseWriter)\b"""
        ),
    },
    "http-response": {
        _PY: re.compile(r"""\b(?:JSONResponse|HTMLResponse|PlainTextResponse|RedirectResponse|StreamingResponse|HttpResponse|JsonResponse|make_response|jsonify)\("""),
        _JS: re.compile(r"""\b(?:res|response|reply)\.(?:status|json|send|sendStatus|redirect|code)\(|\bNextResponse\.(?:json|redirect)\(|\bnew\s+Response\("""),
        _PHP: re.compile(r"""\bresponse\(\)\s*->\s*(?:json|view|download|redirect)\(|\bnew\s+JsonResponse\(|\breturn\s+response\("""),
        _RB: re.compile(r"""^[ \t]*render\s+(?:json|plain|status|html):|\bhead\s+:(?:ok|not_found|no_content|unprocessable_entity)\b"""),
        _JAVA: re.compile(r"""\bResponseEntity\.(?:ok|status|badRequest|notFound|noContent|created)\(|\bnew\s+ResponseEntity<"""),
        _CS: re.compile(r"""\bnew\s+(?:OkObjectResult|JsonResult|ObjectResult|ContentResult)\(|\bResults\.(?:Ok|Json|NotFound|BadRequest)\("""),
        _GO: re.compile(r"""\bw\.WriteHeader\(|\bc\.(?:JSON|String|Status)\(\s*http\.Status"""),
    },
    "env": {
        _PY: re.compile(r"""\bos\.(?:environ\b|getenv\()"""),
        _JS: re.compile(r"""\bprocess\.env\b|\bimport\.meta\.env\b|\bDeno\.env\."""),
        _PHP: re.compile(r"""(?<![\w>:$])(?:env|getenv)\(\s*['"]|\$_ENV\["""),
        _RB: re.compile(r"""\bENV(?:\[|\.fetch\()"""),
        _JAVA: re.compile(r"""\bSystem\.getenv\("""),
        _CS: re.compile(r"""\bEnvironment\.GetEnvironmentVariable\("""),
        _GO: re.compile(r"""\bos\.(?:Getenv|LookupEnv|Environ)\("""),
    },
    "filesystem": {
        _PY: re.compile(r"""^[ \t]*(?:from|import)\s+shutil\b|(?<![\w.])open\(|\.(?:read_text|write_text|read_bytes|write_bytes)\(|\bos\.(?:remove|unlink|makedirs|mkdir|rename)\("""),
        _JS: re.compile(r"""(?:from\s+|require\(\s*)['"](?:node:)?fs(?:/promises)?['"]|(?:from\s+|require\(\s*)['"]fs-extra['"]"""),
        _PHP: re.compile(r"""\b(?:file_get_contents|file_put_contents|fopen|unlink|mkdir)\(|\bStorage::"""),
        _RB: re.compile(r"""\bFile\.(?:open|read|write|delete)\(|\bFileUtils\."""),
        _JAVA: re.compile(r"""^[ \t]*import\s+(?:java\.nio\.file\.|java\.io\.File(?:Reader|Writer|InputStream|OutputStream)?;)"""),
        _CS: re.compile(r"""\bFile\.(?:ReadAllText|WriteAllText|ReadAllBytes|WriteAllBytes|Open|Delete|Exists)\(|\bDirectory\.\w+\("""),
        _GO: re.compile(r"""\bos\.(?:Open|OpenFile|Create|ReadFile|WriteFile|Remove|RemoveAll|MkdirAll)\(|\bioutil\.(?:ReadFile|WriteFile)\("""),
    },
    "process": {
        _PY: re.compile(r"""^[ \t]*(?:from|import)\s+subprocess\b|\bos\.(?:system|popen|exec\w*)\("""),
        _JS: re.compile(r"""(?:from\s+|require\(\s*)['"](?:node:)?child_process['"]|(?:from\s+|require\(\s*)['"]execa['"]"""),
        _PHP: re.compile(r"""(?<![\w>:$])(?:exec|shell_exec|system|passthru|proc_open|popen)\(|\bProcess::"""),
        _RB: re.compile(r"""\bOpen3\.|(?<![\w.])system\(|\bspawn\("""),
        _JAVA: re.compile(r"""\bnew\s+ProcessBuilder\(|\bRuntime\.getRuntime\(\)\.exec\("""),
        _CS: re.compile(r"""\bProcess\.Start\("""),
        _GO: re.compile(r""""os/exec"|\bexec\.Command(?:Context)?\("""),
    },
}


def _family(extension: str, table: dict[tuple[str, ...], re.Pattern[str]]) -> re.Pattern[str] | None:
    return next((pattern for extensions, pattern in table.items() if extension in extensions), None)


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def supported(path: str) -> bool:
    return _extension(path) in _COMMENT


def _evidence(line: str) -> str:
    text = " ".join(line.split())
    return text if len(text) <= EVIDENCE_LENGTH else text[: EVIDENCE_LENGTH - 3] + "..."


def _sql_literal(line: str, pattern: re.Pattern[str]) -> bool:
    """A string literal on the line that starts a SQL statement (or a triple quote opening one)."""
    for match in _LITERAL.finditer(line):
        body = match.group(0)
        if body in ('"""', "'''"):
            if pattern.match(" " + line[match.end():]):
                return True
            continue
        if pattern.match(" " + body[1:-1].lstrip()):
            return True
    return False


def detect(path: str, text: str) -> tuple[Finding, ...]:
    """The capabilities of one source file, at most one finding per capability and line.

    Each pattern runs once over the whole text; only the lines it hits are looked at.
    """
    extension = _extension(path)
    comments = _COMMENT.get(extension)
    if comments is None:
        return ()
    lines = [line.rstrip("\r") for line in text.split("\n")]
    joined = "\n".join(lines)
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)

    def hits(pattern: re.Pattern[str] | None, triggers: tuple[str, ...]) -> set[int]:
        """Lines the pattern matches; it runs only on the lines that hold one of its trigger words."""
        if pattern is None:
            return set()
        candidates: set[int] = set()
        for trigger in triggers:
            position = joined.find(trigger)
            while position >= 0:
                number = bisect.bisect_right(starts, position)
                candidates.add(number)
                position = joined.find(trigger, starts[number] if number < len(starts) else len(joined))
        return {number for number in candidates if pattern.search(lines[number - 1])}

    delimiters = _MULTILINE_STRINGS.get(extension, ())
    inside: list[str] = []      # per line: the multi-line string it starts in, or ""
    state = ""
    for line in lines:
        inside.append(state)
        if delimiters and (state or any(d in line for d in delimiters)):
            state = _string_state(line, state, delimiters)
    api_lines = hits(_family(extension, _SQL_API), _TRIGGERS["sql-api"])
    sql_lines = hits(_SQL_START, _TRIGGERS["sql"])
    by_capability = {capability: hits(_family(extension, table), _TRIGGERS[capability])
                     for capability, table in _IMPORTS.items()}
    found: list[Finding] = []
    for number in sorted(api_lines | sql_lines | set().union(*by_capability.values())):
        raw = lines[number - 1]
        stripped = raw.lstrip()
        if inside[number - 1]:
            # Text of a string (a docstring, a template of another program): only the SQL of a query
            # call opened just above counts.
            if number in sql_lines and _SQL_START.match(" " + stripped) and _opens_query_call(lines, number):
                found.append(Finding("sql", number, _evidence(raw)))
            continue
        if not stripped or stripped.startswith(comments):
            continue
        evidence = _evidence(raw)
        seen: set[str] = set()
        if number in api_lines:
            found.append(Finding("sql", number, evidence))
            seen.add("sql")
        elif number in sql_lines:
            in_call = bool(_SQL_CALL.search(raw)) or _opens_query_call(lines, number)
            if in_call and (_sql_literal(raw, _SQL_START) or _SQL_START.match(" " + stripped)):
                found.append(Finding("sql", number, evidence))
                seen.add("sql")
            elif _sql_literal(raw, _SQL_START_UPPER):
                found.append(Finding("sql", number, evidence, suspect=True))
                seen.add("sql")
        # `import type` is erased by the compiler: the file names the client's types, it does not call it.
        types_only = extension in _JS and stripped.startswith("import type ")
        for capability, numbers in by_capability.items():
            if capability not in seen and number in numbers:
                found.append(Finding(capability, number, evidence, suspect=types_only))
                seen.add(capability)
    return tuple(found)


_IMPORT_WORDS = ("import", "require", "from ", "use ", "using ", "database/sql", "gorm.io", "sqlx", "pgx", "mongo-driver",
                 "go-sql-driver", "lib/pq", "go-resty", "gin-gonic", "labstack", "gofiber", "go-chi", "gorilla", "os/exec")
# Words without which a pattern cannot match a line: the patterns run only on the lines that hold one
# (a plain substring search over the file is far faster than a regular expression at every position).
_TRIGGERS: dict[str, tuple[str, ...]] = {
    "sql": ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE", "MERGE",
            "select", "insert", "update", "delete", "create", "alter", "drop", "truncate", "merge"),
    "sql-api": ("session", "Session", "cursor", "prisma.", "createQueryBuilder", "knex(", "db.", "tx.", "trx.", "conn.",
                "pool.", "selectFrom", "insertInto", "updateTable", "deleteFrom", "sql`", "DB::", "->prepare", "->exec",
                "find_by_sql", "connection.", "Arel.sql", "createQuery", "createNativeQuery", "prepareStatement",
                "jdbcTemplate", "@Query", "FromSql", "ExecuteSql", "SqlCommand"),
    "db-client": _IMPORT_WORDS + ("PDO", "ActiveRecord"),
    "http-client": _IMPORT_WORDS + ("fetch(", "curl_init", "Http::", "Net::HTTP", "Faraday", "HTTParty", "RestClient",
                                    "HttpClient", "http.Get", "http.Post", "http.Head", "http.NewRequest"),
    "framework": _IMPORT_WORDS + ("Controller", "Sinatra", "http.HandleFunc", "http.ResponseWriter"),
    "http-response": ("Response", "make_response", "jsonify", "res.", "response", "reply.", "render ", "head :",
                      "Result(", "Results.", "w.WriteHeader", "c.JSON", "c.String", "c.Status"),
    "env": ("environ", "getenv", "env", "ENV", "Environment", "Getenv", "LookupEnv", "Environ"),
    "filesystem": _IMPORT_WORDS + ("open(", "_text(", "_bytes(", "os.", "file_", "fopen", "unlink(", "mkdir(", "Storage::",
                                   "File.", "FileUtils", "Directory.", "ioutil."),
    "process": _IMPORT_WORDS + ("os.", "exec", "system(", "passthru", "proc_open", "popen(", "Process", "Open3",
                                "spawn(", "Runtime.getRuntime"),
}


_MULTILINE_STRINGS = {
    **{ext: ('"""', "'''") for ext in _PY},
    **{ext: ("`",) for ext in _JS + _GO},
    **{ext: ('"""',) for ext in _JAVA + _CS},
}


def _string_state(line: str, open_string: str, delimiters: tuple[str, ...]) -> str:
    """The multi-line string open at the end of `line`, given the one open at its start ("" for none)."""
    index = 0
    while index < len(line):
        if open_string:
            end = line.find(open_string, index)
            while end > 0 and line[end - 1] == "\\" and open_string == "`":
                end = line.find(open_string, end + 1)
            if end < 0:
                return open_string
            index, open_string = end + len(open_string), ""
            continue
        starts = [(line.find(d, index), d) for d in delimiters if line.find(d, index) >= 0]
        if not starts:
            return ""
        position, delimiter = min(starts)
        if "#" in line[index:position] or "//" in line[index:position]:
            return ""   # the rest is a comment
        index, open_string = position + len(delimiter), delimiter
    return open_string


def _opens_query_call(lines: list[str], number: int) -> bool:
    """The SQL literal on line `number` is an argument of a query call opened on one of the three lines above."""
    for back in range(1, 4):
        index = number - 1 - back
        if index < 0:
            break
        above = lines[index]
        if _SQL_CALL.search(above) and above.count("(") > above.count(")"):
            return True
    return False


def rules_from_toml(table: object) -> tuple[CapabilityRule, ...]:
    """[capabilities] of a style file, or `capabilities:` of a part in .wingbox/config.yaml."""
    if not isinstance(table, dict):
        return ()
    rules = []
    for capability, value in table.items():
        if capability not in CAPABILITIES or not isinstance(value, dict):
            continue
        rules.append(CapabilityRule(
            capability=str(capability),
            allowed_in=tuple(str(item) for item in value.get("allowed_in", []) or ()),
            forbidden_in=tuple(str(item) for item in value.get("forbidden_in", []) or ()),
            fix=str(value.get("fix", "")),
        ))
    return tuple(rules)


def merge_rules(style: tuple[CapabilityRule, ...], stated: tuple[CapabilityRule, ...]) -> tuple[CapabilityRule, ...]:
    """The rules of a part: the style's, each replaced by the one the config states for the same capability."""
    by_capability = {rule.capability: rule for rule in style}
    for rule in stated:
        base = by_capability.get(rule.capability)
        by_capability[rule.capability] = CapabilityRule(rule.capability, rule.allowed_in, rule.forbidden_in,
                                                        rule.fix or (base.fix if base else ""))
    return tuple(by_capability[name] for name in CAPABILITIES if name in by_capability)
