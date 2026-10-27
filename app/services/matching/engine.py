import re

from rapidfuzz import fuzz

from app.core.enums import MatchMethod
from app.services.matching.client_query import (
    canonical_search_text,
    extract_client_identity_text,
    numeric_tokens,
    outlet_tokens,
    text_without_numbers,
)
from app.services.matching.normalizer import contains_phrase, normalize_text
from app.services.matching.types import MatchCandidate, MatchResult, RankedMatch
from app.services.plan_aliases import address_alias_text, is_address_alias

_NUMERIC_TOKEN = re.compile(r"\d+")


class MatchingEngine:
    """Explainable client matcher for noisy human WhatsApp messages.

    Human identity signals are deliberately stronger than 1C catalog-code
    suffixes. Agents normally identify outlets by a name, an outlet number, or
    an address, not by the internal Catalog_Партнеры.Code.

    Matching signals, in priority order:
      * complete 1C code (explicit admin/test input)
      * exact human alias
      * outlet/branch identifier + name (N0158 == 158; S/K/D prefixes stay distinct)
      * address evidence
      * exact client name
      * fuzzy human name/alias
      * short 1C-code suffix as a last-resort compatibility fallback

    Agent-plan membership is a preference, not a hard filter: a new client can
    legitimately be absent from the selected agent plan and still needs to be
    matched so the 1C writer can append a new row.
    """

    def __init__(
        self,
        *,
        fuzzy_threshold: float = 88.0,
        ambiguity_margin: float = 5.0,
    ) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_margin = ambiguity_margin

    def match(
        self,
        raw_text: str,
        candidates: list[MatchCandidate],
        *,
        preferred_one_c_refs: set[str] | frozenset[str] | None = None,
    ) -> MatchResult:
        text = normalize_text(raw_text)
        identity = extract_client_identity_text(raw_text) or text
        if not identity or not candidates:
            return MatchResult(matched=False, ambiguous=False)

        preferred = {
            str(ref).strip().lower()
            for ref in (preferred_one_c_refs or set())
            if str(ref).strip()
        }

        full_code_matches = self._full_code_matches(identity, candidates)
        alias_matches = self._alias_matches(identity, candidates)
        name_matches = self._name_matches(identity, candidates)

        # Explicit complete 1C codes remain authoritative, but a conflicting
        # exact human identity is suspicious and therefore requires review.
        if full_code_matches:
            descriptive = self._dedupe_by_client([*name_matches, *alias_matches])
            desc_ids = {item.client_id for item in descriptive}
            if desc_ids:
                intersection = [item for item in full_code_matches if item.client_id in desc_ids]
                if len(intersection) == 1:
                    return self._result(intersection[0])
                if not intersection:
                    return self._ambiguous(
                        self._dedupe_by_client([*full_code_matches, *descriptive])
                    )
                return self._ambiguous(intersection)
            if len(full_code_matches) == 1:
                return self._result(full_code_matches[0])
            return self._prefer_or_ambiguous(full_code_matches, candidates, preferred)

        # Meaningful branch prefixes (S/K/D/...) are authoritative human
        # identifiers. If the query names a branch code and one client carries
        # that exact code together with compatible name evidence, select it
        # before generic aliases/fuzzy logic. N remains an optional marker and
        # is canonicalized to the plain number, so it is intentionally excluded
        # from this strong branch rule.
        strong_branch_matches = self._strong_branch_code_matches(identity, candidates)
        if strong_branch_matches:
            if len(strong_branch_matches) == 1:
                return self._result(strong_branch_matches[0])
            preferred_result = self._single_preferred(
                strong_branch_matches, candidates, preferred
            )
            if preferred_result is not None:
                return self._result(
                    preferred_result,
                    alternatives=[
                        item
                        for item in strong_branch_matches
                        if item.client_id != preferred_result.client_id
                    ][:2],
                )
            return self._ambiguous(strong_branch_matches[:3])

        # Several clients sharing one exact name/alias (typically branches of
        # one chain, e.g. "ორი ნაბიჯი") are not decided yet: outlet, address
        # and fuzzy evidence below get a chance to pick the branch. If none of
        # them does, this ambiguity is returned before the legacy short-code
        # fallback.
        pending_ambiguity: list[RankedMatch] = []

        # With no numeric outlet/code token, preserve the semantic preference
        # for a complete exact name over a shorter alias.
        if name_matches and not outlet_tokens(identity):
            if len(name_matches) == 1:
                return self._result(name_matches[0])
            preferred_result = self._single_preferred(name_matches, candidates, preferred)
            if preferred_result is not None:
                return self._result(preferred_result)
            pending_ambiguity = name_matches

        # Exact human aliases (for example "გვირილა 0203") are stronger than
        # any internal 1C-code suffix.
        if alias_matches and not pending_ambiguity:
            if len(alias_matches) == 1:
                return self._result(alias_matches[0])
            preferred_result = self._single_preferred(alias_matches, candidates, preferred)
            if preferred_result is not None:
                return self._result(preferred_result)
            pending_ambiguity = alias_matches

        # Outlet/store number + human name. This also looks at the display name,
        # so "დეილი N0158" can be found from "დეილი 158" even when the client
        # has never appeared in the selected agent plan.
        outlet_matches = self._outlet_matches(identity, candidates, preferred)
        if outlet_matches:
            decided = self._decide_ranked(
                outlet_matches,
                candidates,
                preferred,
                min_score=86.0,
                preferred_min_score=84.0,
                margin=7.0,
            )
            if decided is not None:
                return decided
            return self._ambiguous(outlet_matches[:3])

        # Address aliases are typed separately ("addr ...") so street/building
        # numbers can never be mistaken for outlet numbers.
        address_matches = self._address_matches(identity, candidates, preferred)
        if address_matches:
            decided = self._decide_ranked(
                address_matches,
                candidates,
                preferred,
                min_score=90.0,
                preferred_min_score=87.0,
                margin=8.0,
            )
            if decided is not None:
                return decided
            # Strong address evidence exists but is not unique enough. Do not
            # fall through to a looser fuzzy guess.
            if address_matches[0].score >= 82.0:
                return self._ambiguous(address_matches[:3])

        # Exact names are safe if unique. Preserve legacy short-code evidence
        # only when it points to the SAME exact-name client; it can never
        # override a different human name/address candidate.
        short_code_matches = self._short_code_matches(identity, candidates)
        if name_matches and not pending_ambiguity:
            if len(name_matches) == 1:
                same_client_code = [
                    item
                    for item in short_code_matches
                    if item.client_id == name_matches[0].client_id
                ]
                if len(same_client_code) == 1:
                    return self._result(same_client_code[0])
                return self._result(name_matches[0])
            preferred_result = self._single_preferred(name_matches, candidates, preferred)
            if preferred_result is not None:
                return self._result(preferred_result)
            # Do not immediately stop here: a fuzzy alias can sometimes carry
            # extra locality information. We keep the duplicate names as a
            # fallback ambiguity below.

        fuzzy_matches = self._fuzzy_matches(identity, candidates, preferred)
        if fuzzy_matches and fuzzy_matches[0].score >= self.fuzzy_threshold:
            top = fuzzy_matches[0]
            if len(fuzzy_matches) == 1:
                return self._result(top)
            second = fuzzy_matches[1]
            if top.client_id == second.client_id or top.score - second.score >= self.ambiguity_margin:
                return self._result(top, alternatives=fuzzy_matches[1:3])

        if pending_ambiguity:
            return self._ambiguous(pending_ambiguity)
        if name_matches:
            return self._ambiguous(name_matches)

        # Compatibility only: allow legacy short 1C-code suffixes after every
        # human-facing signal has failed. This prevents "158" from hijacking a
        # perfectly good name/address match just because a random 1C code ends
        # in 158.
        if short_code_matches:
            if len(short_code_matches) == 1:
                return self._result(short_code_matches[0])
            preferred_result = self._single_preferred(short_code_matches, candidates, preferred)
            if preferred_result is not None:
                return self._result(preferred_result)
            disambiguated = self._disambiguate_subset_by_name(
                identity,
                short_code_matches,
                candidates,
                preferred,
            )
            if disambiguated is not None:
                return self._result(disambiguated)
            return self._ambiguous(short_code_matches)

        return MatchResult(
            matched=False,
            ambiguous=False,
            alternatives=tuple(fuzzy_matches[:3]),
        )

    def rank_for_review(
        self,
        raw_text: str,
        candidates: list[MatchCandidate],
        *,
        preferred_one_c_refs: set[str] | frozenset[str] | None = None,
        limit: int = 5,
    ) -> tuple[RankedMatch, ...]:
        """Return deterministic top candidates for review/observability.

        This never calls AI and never changes the actual matching decision. It
        reuses the same human identity signals as ``match`` so the candidates
        shown to a WhatsApp user are real local DB clients and follow the same
        outlet-code normalization rules.
        """
        text = normalize_text(raw_text)
        identity = extract_client_identity_text(raw_text) or text
        if not identity or not candidates or limit <= 0:
            return ()

        preferred = {
            str(ref).strip().lower()
            for ref in (preferred_one_c_refs or set())
            if str(ref).strip()
        }
        ranked = self._dedupe_by_client(
            [
                *self._full_code_matches(identity, candidates),
                *self._alias_matches(identity, candidates),
                *self._outlet_matches(identity, candidates, preferred),
                *self._address_matches(identity, candidates, preferred),
                *self._name_matches(identity, candidates),
                *self._fuzzy_matches(identity, candidates, preferred),
                *self._short_code_matches(identity, candidates),
            ]
        )
        ranked.sort(
            key=lambda item: (
                item.score,
                self._ranked_is_preferred(item, candidates, preferred),
            ),
            reverse=True,
        )
        return tuple(ranked[:limit])

    def _full_code_matches(
        self,
        text: str,
        candidates: list[MatchCandidate],
    ) -> list[RankedMatch]:
        results: list[RankedMatch] = []
        for candidate in candidates:
            if not candidate.code:
                continue
            normalized_code = normalize_text(candidate.code)
            if normalized_code and contains_phrase(text, normalized_code):
                results.append(
                    RankedMatch(
                        candidate.client_id,
                        MatchMethod.CODE,
                        100.0,
                        f"code:{candidate.code}",
                    )
                )
        return self._dedupe_by_client(results)

    def _short_code_matches(
        self,
        text: str,
        candidates: list[MatchCandidate],
    ) -> list[RankedMatch]:
        results: list[RankedMatch] = []
        numeric = [token for token in _NUMERIC_TOKEN.findall(text) if len(token) >= 3]

        for candidate in candidates:
            if not candidate.code:
                continue

            normalized_code = normalize_text(candidate.code)
            if normalized_code and contains_phrase(text, normalized_code):
                continue

            candidate_digits = "".join(ch for ch in candidate.code if ch.isdigit())
            if not candidate_digits:
                continue

            for token in numeric:
                if len(token) <= len(candidate_digits) and candidate_digits.endswith(token):
                    results.append(
                        RankedMatch(
                            candidate.client_id,
                            MatchMethod.CODE,
                            78.0,
                            f"legacy-short-code:{token}->{candidate.code}",
                        )
                    )
                    break

        return self._dedupe_by_client(results)

    @staticmethod
    def _meaningful_branch_tokens(value: str) -> set[str]:
        return {
            token
            for token in outlet_tokens(value)
            if re.fullmatch(r"[a-mo-z]\d+", token, flags=re.IGNORECASE)
        }

    def _strong_branch_code_matches(
        self,
        identity: str,
        candidates: list[MatchCandidate],
    ) -> list[RankedMatch]:
        query_codes = self._meaningful_branch_tokens(identity)
        if not query_codes:
            return []

        query_name = canonical_search_text(text_without_numbers(identity))
        if not query_name:
            return []

        matches: list[RankedMatch] = []
        for candidate in candidates:
            best: RankedMatch | None = None
            terms = [candidate.normalized_name]
            terms.extend(alias for alias in candidate.aliases if not is_address_alias(alias))
            for raw_term in terms:
                term_codes = self._meaningful_branch_tokens(raw_term)
                overlap = query_codes & term_codes
                if not overlap:
                    continue

                term_name = canonical_search_text(text_without_numbers(raw_term))
                name_score = self._name_similarity(query_name, term_name)
                # The branch code is exact, but still require enough descriptive
                # name evidence to avoid selecting an unrelated chain that happens
                # to reuse the same branch token.
                if name_score < 70.0:
                    continue

                score = min(100.0, 96.0 + (name_score / 25.0))
                current = RankedMatch(
                    candidate.client_id,
                    MatchMethod.EXACT_BRANCH,
                    score,
                    f"exact-branch:{'/'.join(sorted(overlap))};name={name_score:.1f};term={raw_term}",
                )
                if best is None or current.score > best.score:
                    best = current
            if best is not None:
                matches.append(best)

        return self._dedupe_by_client(matches)

    def _alias_matches(
        self,
        text: str,
        candidates: list[MatchCandidate],
    ) -> list[RankedMatch]:
        query = canonical_search_text(text)
        query_numbers = set(outlet_tokens(query))
        results: list[RankedMatch] = []

        for candidate in candidates:
            best: RankedMatch | None = None
            for alias in candidate.aliases:
                if is_address_alias(alias):
                    continue

                normalized_alias = canonical_search_text(alias)
                if not normalized_alias or not contains_phrase(query, normalized_alias):
                    continue

                alias_numbers = set(outlet_tokens(normalized_alias))

                # A generic chain/name alias such as "ორი ნაბიჯი" must not
                # become an "exact alias" merely because the user wrote
                # "ორი ნაბიჯი 391". Otherwise every branch of the same chain
                # is returned at score 100 and the stronger outlet/address
                # evidence never gets a chance to decide the client.
                #
                # When the query contains a human branch/store number, only an
                # alias carrying the same number is strong enough for this
                # exact-alias stage. Generic name aliases are handled later by
                # outlet/address/fuzzy matching.
                if query_numbers:
                    if not alias_numbers or not (query_numbers & alias_numbers):
                        continue

                score = 100.0 if query == normalized_alias else 99.0
                current = RankedMatch(
                    candidate.client_id,
                    MatchMethod.ALIAS,
                    score,
                    f"alias:{alias}",
                )
                if best is None or current.score > best.score:
                    best = current

            if best is not None:
                results.append(best)

        return self._dedupe_by_client(results)

    def _name_matches(
        self,
        text: str,
        candidates: list[MatchCandidate],
    ) -> list[RankedMatch]:
        """Return safe exact-name matches for the human client identity.

        1C display names can contain a technical prefix (for example ``შმს``)
        and/or address metadata after `` * ``. Agents normally omit those
        decorations in WhatsApp. Those formatting differences must not force a
        fuzzy review when the underlying identity is exact.

        Examples treated as exact:
          * ``1სოგოი`` == ``შმს 1სოგოი``
          * ``ივო 87`` == ``შმს ივო 87 * ქ.თბილისი, ...``

        This remains conservative: only known leading technical tokens are
        stripped, and only the explicit `` * `` metadata separator is removed.
        If more than one client reduces to the same identity, the normal
        ambiguity logic still asks the user to choose.
        """

        technical_prefixes = {"შმს", "შპს"}

        def identity_variants(value: str) -> tuple[str, ...]:
            raw = str(value or "").strip()
            if not raw:
                return ()

            raw_variants = [raw]
            # In 1C names the spaced star is metadata/address decoration. Keep
            # branch identifiers like ``*S278`` intact because they do not use
            # the spaced separator.
            if " * " in raw:
                raw_variants.append(raw.split(" * ", 1)[0].strip())

            result: list[str] = []
            for item in raw_variants:
                canonical = canonical_search_text(item)
                if not canonical:
                    continue
                if canonical not in result:
                    result.append(canonical)

                tokens = canonical.split()
                if tokens and tokens[0] in technical_prefixes:
                    stripped = " ".join(tokens[1:]).strip()
                    if stripped and stripped not in result:
                        result.append(stripped)
            return tuple(result)

        query_variants = identity_variants(text)
        if not query_variants:
            return []

        results: list[RankedMatch] = []
        for candidate in candidates:
            candidate_variants: list[str] = []
            for source in (candidate.display_name, candidate.normalized_name):
                for variant in identity_variants(source):
                    if variant not in candidate_variants:
                        candidate_variants.append(variant)

            # A candidate identity counts as exact when it appears as a whole
            # phrase inside the client part of the message. Equality (the v41
            # rule for "1სოგოი" == "შმს 1სოგოი") is the special case where the
            # phrase is the entire query.
            overlap = {
                variant
                for variant in candidate_variants
                if any(contains_phrase(query, variant) for query in query_variants)
            }
            if not overlap:
                continue

            evidence_value = sorted(overlap, key=len, reverse=True)[0]
            results.append(
                RankedMatch(
                    candidate.client_id,
                    MatchMethod.EXACT_NAME,
                    100.0,
                    f"name-exact:{evidence_value}->{candidate.display_name}",
                )
            )

        return self._dedupe_by_client(results)

    def _outlet_matches(
        self,
        identity: str,
        candidates: list[MatchCandidate],
        preferred: set[str],
    ) -> list[RankedMatch]:
        query_numbers = set(outlet_tokens(identity))
        if not query_numbers:
            return []

        query_name = canonical_search_text(text_without_numbers(identity))
        query_canonical = canonical_search_text(identity)
        matches: list[RankedMatch] = []

        for candidate in candidates:
            best_score = -1.0
            best_evidence = ""
            in_plan = self._is_preferred(candidate, preferred)
            terms = [candidate.normalized_name]
            terms.extend(alias for alias in candidate.aliases if not is_address_alias(alias))

            for raw_term in terms:
                term = canonical_search_text(raw_term)
                term_numbers = set(outlet_tokens(raw_term))
                overlap = query_numbers & term_numbers
                if not overlap:
                    continue

                term_name = canonical_search_text(text_without_numbers(raw_term))
                name_score = self._name_similarity(query_name, term_name)

                if query_name:
                    score = 58.0 + (0.42 * name_score)
                    if query_canonical == term or contains_phrase(term, query_canonical):
                        score = max(score, 98.0)
                else:
                    score = 80.0

                if in_plan:
                    score += 4.0
                score = min(score, 100.0)

                if score > best_score:
                    best_score = score
                    best_evidence = (
                        f"outlet:{'/'.join(sorted(overlap))};"
                        f"name={name_score:.1f};plan={in_plan};term={raw_term}"
                    )

            if best_score >= 0:
                matches.append(
                    RankedMatch(
                        candidate.client_id,
                        MatchMethod.FUZZY,
                        best_score,
                        best_evidence,
                    )
                )

        return sorted(
            self._dedupe_by_client(matches),
            key=lambda item: (
                self._ranked_is_preferred(item, candidates, preferred),
                item.score,
            ),
            reverse=True,
        )

    def _address_matches(
        self,
        identity: str,
        candidates: list[MatchCandidate],
        preferred: set[str],
    ) -> list[RankedMatch]:
        query = canonical_search_text(identity)
        if not query:
            return []

        query_tokens = query.split()
        query_numbers = numeric_tokens(query)
        results: list[RankedMatch] = []

        for candidate in candidates:
            best_score = -1.0
            best_alias = ""
            in_plan = self._is_preferred(candidate, preferred)

            for alias in candidate.aliases:
                if not is_address_alias(alias):
                    continue
                address = canonical_search_text(address_alias_text(alias))
                if not address:
                    continue

                score = max(
                    float(fuzz.WRatio(query, address)),
                    float(fuzz.token_set_ratio(query, address)),
                    float(fuzz.partial_ratio(query, address)),
                )
                if contains_phrase(address, query):
                    score = max(score, 98.0)

                # Exact street/building numbers are powerful human signals when
                # accompanied by at least one descriptive word. This is what
                # lets "ფრიდონ ხალვაშის 101გ ორი ნაბიჯების" beat another
                # nearby "ორი ნაბიჯი" branch, while a bare "16" still never
                # auto-selects a client.
                address_numbers = set(numeric_tokens(address))
                number_overlap = set(query_numbers) & address_numbers
                descriptive_tokens = [
                    token for token in query_tokens if not any(ch.isdigit() for ch in token)
                ]
                if number_overlap and descriptive_tokens:
                    score = min(100.0, score + 8.0)

                # A single generic word (for example just "თბილისი") is too
                # weak for automatic selection. A number or 2+ tokens makes an
                # address query much more informative.
                if len(query_tokens) == 1 and not query_numbers:
                    score = min(score, 84.0)

                if in_plan:
                    score = min(100.0, score + 4.0)

                if score > best_score:
                    best_score = score
                    best_alias = address

            if best_score >= 0:
                results.append(
                    RankedMatch(
                        candidate.client_id,
                        MatchMethod.FUZZY,
                        best_score,
                        f"address:{best_alias};plan={in_plan}",
                    )
                )

        return sorted(
            self._dedupe_by_client(results),
            key=lambda item: (
                self._ranked_is_preferred(item, candidates, preferred),
                item.score,
            ),
            reverse=True,
        )

    def _fuzzy_matches(
        self,
        text: str,
        candidates: list[MatchCandidate],
        preferred: set[str] | None = None,
    ) -> list[RankedMatch]:
        best_by_client: dict[object, RankedMatch] = {}
        preferred = preferred or set()
        query = canonical_search_text(text)

        for candidate in candidates:
            terms = [candidate.normalized_name]
            terms.extend(alias for alias in candidate.aliases if not is_address_alias(alias))
            for raw_term in terms:
                term = canonical_search_text(raw_term)
                if not term:
                    continue

                score = self._fuzzy_score(query, term)
                if self._is_preferred(candidate, preferred):
                    score = min(100.0, score + 3.0)

                current = best_by_client.get(candidate.client_id)
                if current is None or score > current.score:
                    best_by_client[candidate.client_id] = RankedMatch(
                        candidate.client_id,
                        MatchMethod.FUZZY,
                        score,
                        f"fuzzy:{raw_term};plan={self._is_preferred(candidate, preferred)}",
                    )

        return sorted(
            best_by_client.values(),
            key=lambda item: item.score,
            reverse=True,
        )

    def _disambiguate_subset_by_name(
        self,
        identity: str,
        subset: list[RankedMatch],
        candidates: list[MatchCandidate],
        preferred: set[str],
    ) -> RankedMatch | None:
        ids = {item.client_id for item in subset}
        candidate_by_id = {candidate.client_id: candidate for candidate in candidates}
        query_name = canonical_search_text(text_without_numbers(identity))
        if not query_name:
            return None

        ranked: list[RankedMatch] = []
        for client_id in ids:
            candidate = candidate_by_id.get(client_id)
            if candidate is None:
                continue
            terms = [candidate.normalized_name]
            terms.extend(alias for alias in candidate.aliases if not is_address_alias(alias))
            name_score = max(
                (
                    self._name_similarity(
                        query_name,
                        canonical_search_text(text_without_numbers(term)),
                    )
                    for term in terms
                ),
                default=0.0,
            )
            if self._is_preferred(candidate, preferred):
                name_score = min(100.0, name_score + 4.0)
            ranked.append(
                RankedMatch(
                    client_id,
                    MatchMethod.CODE,
                    name_score,
                    f"legacy-short-code+name:{name_score:.1f}",
                )
            )

        ranked.sort(key=lambda item: item.score, reverse=True)
        if not ranked or ranked[0].score < 75.0:
            return None
        if len(ranked) > 1 and ranked[0].score - ranked[1].score < 8.0:
            return None
        return ranked[0]

    def _decide_ranked(
        self,
        matches: list[RankedMatch],
        candidates: list[MatchCandidate],
        preferred: set[str],
        *,
        min_score: float,
        preferred_min_score: float,
        margin: float,
    ) -> MatchResult | None:
        preferred_result = self._single_preferred(matches, candidates, preferred)
        if preferred_result is not None and preferred_result.score >= preferred_min_score:
            # A single preferred candidate is useful context, but if a global
            # candidate has an almost identical score we still require review.
            competing = [m for m in matches if m.client_id != preferred_result.client_id]
            if not competing or preferred_result.score - competing[0].score >= 3.0:
                return self._result(preferred_result, alternatives=competing[:2])

        top = matches[0]
        if top.score < min_score:
            return None
        if len(matches) == 1:
            return self._result(top)
        second = matches[1]
        if top.client_id == second.client_id or top.score - second.score >= margin:
            return self._result(top, alternatives=matches[1:3])
        return None

    @staticmethod
    def _name_similarity(left: str, right: str) -> float:
        left = canonical_search_text(left)
        right = canonical_search_text(right)
        if not left or not right:
            return 0.0
        return max(
            float(fuzz.ratio(left, right)),
            float(fuzz.token_set_ratio(left, right)),
            float(fuzz.WRatio(left, right)),
        )

    @staticmethod
    def _fuzzy_score(text: str, term: str) -> float:
        text = canonical_search_text(text)
        term = canonical_search_text(term)
        text_tokens = text.split()
        term_tokens = term.split()
        if not text_tokens or not term_tokens:
            return 0.0

        target_width = len(term_tokens)
        widths = {
            width
            for width in (target_width - 1, target_width, target_width + 1)
            if 1 <= width <= len(text_tokens)
        }

        best = 0.0
        for width in widths:
            for start in range(0, len(text_tokens) - width + 1):
                window = " ".join(text_tokens[start : start + width])
                score = max(
                    float(fuzz.ratio(term, window)),
                    float(fuzz.token_set_ratio(term, window)),
                )
                best = max(best, score)
        return best

    def _prefer_or_ambiguous(
        self,
        matches: list[RankedMatch],
        candidates: list[MatchCandidate],
        preferred: set[str],
    ) -> MatchResult:
        preferred_result = self._single_preferred(matches, candidates, preferred)
        if preferred_result is not None:
            return self._result(preferred_result)
        return self._ambiguous(matches)

    @staticmethod
    def _single_preferred(
        matches: list[RankedMatch],
        candidates: list[MatchCandidate],
        preferred: set[str],
    ) -> RankedMatch | None:
        if not preferred:
            return None
        candidate_by_id = {candidate.client_id: candidate for candidate in candidates}
        selected = [
            match
            for match in matches
            if (candidate_by_id.get(match.client_id) is not None)
            and (candidate_by_id[match.client_id].one_c_ref or "").lower() in preferred
        ]
        if len(selected) == 1:
            return selected[0]
        return None

    @staticmethod
    def _is_preferred(candidate: MatchCandidate, preferred: set[str]) -> bool:
        return bool(candidate.one_c_ref and candidate.one_c_ref.lower() in preferred)

    @classmethod
    def _ranked_is_preferred(
        cls,
        match: RankedMatch,
        candidates: list[MatchCandidate],
        preferred: set[str],
    ) -> bool:
        for candidate in candidates:
            if candidate.client_id == match.client_id:
                return cls._is_preferred(candidate, preferred)
        return False

    @staticmethod
    def _dedupe_by_client(matches: list[RankedMatch]) -> list[RankedMatch]:
        by_client: dict[object, RankedMatch] = {}
        for match in matches:
            current = by_client.get(match.client_id)
            if current is None or match.score > current.score:
                by_client[match.client_id] = match
        return sorted(by_client.values(), key=lambda item: item.score, reverse=True)

    @staticmethod
    def _result(
        match: RankedMatch,
        alternatives: list[RankedMatch] | None = None,
    ) -> MatchResult:
        return MatchResult(
            matched=True,
            ambiguous=False,
            client_id=match.client_id,
            method=match.method,
            score=match.score,
            evidence=match.evidence,
            alternatives=tuple(alternatives or []),
        )

    @staticmethod
    def _ambiguous(matches: list[RankedMatch]) -> MatchResult:
        return MatchResult(
            matched=False,
            ambiguous=True,
            alternatives=tuple(matches[:3]),
        )
