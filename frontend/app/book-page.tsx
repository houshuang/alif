import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useNavigation, useRouter } from "expo-router";
import { StatusBar } from "expo-status-bar";

import { completeBookPassage, getBookPageDetail, lookupReviewWord } from "../lib/api";
import {
  ACTIVE_BOOK_READER_KEY,
  BookPassageDraft,
  BookReaderMode,
  BookReaderPolicy,
  bookPassageDraftKey,
  bookReaderLocationKey,
  bookReaderModeKey,
  countGuidedLearningWords,
  isBookTokenMarked,
  parseBookPassageDraft,
  parseBookReaderLocation,
  positionsForSameGuidedWord,
  positionsForSameUnmappedSurface,
  sameBookToken,
  shortBookGloss,
} from "../lib/book-reader";
import { stripDiacritics } from "../lib/review/tashkeel-evidence";
import { BookPageDetail, BookPageToken, WordLookupResult } from "../lib/types";
import { fontFamily } from "../lib/theme";

const PAPER = "#F3E8D2";
const PAPER_DEEP = "#E8D8BA";
const INK = "#2B241C";
const MUTED = "#766956";
const RULE = "#D8C4A2";
const ACCENT = "#8B4A2B";
const UNKNOWN = "#A33E32";
const LEGACY_MODE_KEY = "@alif:book-reader:mode";
const SPAN_KEY = "@alif:book-reader:span";
const POLICY_KEY = "@alif:book-reader:policy";

function freshClientReviewId(
  storyId: number,
  page: number,
  tokenPositions: number[],
  policy: BookReaderPolicy,
): string {
  const range = tokenPositions.length > 0
    ? `${tokenPositions[0]}-${tokenPositions[tokenPositions.length - 1]}`
    : "empty";
  return `bp:${storyId}:${page}:${range}:${policy}:${Date.now().toString(36)}`;
}

function emptyDraft(
  storyId: number,
  page: number,
  tokenPositions: number[],
  policy: BookReaderPolicy,
): BookPassageDraft {
  return {
    unknownLemmaIds: [],
    unknownTokenPositions: [],
    markedTokenPositions: [],
    dontLearnTokenPositions: [],
    learnTokenPositions: [],
    clientReviewId: freshClientReviewId(storyId, page, tokenPositions, policy),
  };
}

export default function BookPageScreen() {
  const params = useLocalSearchParams<{ storyId: string; page: string; atEnd?: string }>();
  const storyId = Number(params.storyId);
  const pageNumber = Number(params.page || 1);
  const router = useRouter();
  const navigation = useNavigation();
  const [data, setData] = useState<BookPageDetail | null>(null);
  const [offset, setOffset] = useState(0);
  const [span, setSpan] = useState<1 | 2>(1);
  const [mode, setMode] = useState<BookReaderMode>("both");
  const [policy, setPolicy] = useState<BookReaderPolicy>("clean");
  const [draft, setDraft] = useState<BookPassageDraft | null>(null);
  const [draftReady, setDraftReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [selectedToken, setSelectedToken] = useState<BookPageToken | null>(null);
  const [lookup, setLookup] = useState<WordLookupResult | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupError, setLookupError] = useState(false);
  const lookupRequest = useRef(0);
  const modeRequest = useRef(0);
  const passageStartedAt = useRef(Date.now());

  const leaveReader = useCallback(async () => {
    await AsyncStorage.setItem(ACTIVE_BOOK_READER_KEY, JSON.stringify({
      storyId,
      pageNumber,
      active: false,
    })).catch(() => {});
    router.replace("/books");
  }, [pageNumber, router, storyId]);

  useLayoutEffect(() => {
    navigation.setOptions({
      title: data?.story_title_en || data?.story_title_ar || "Reader",
      headerStyle: { backgroundColor: PAPER },
      headerTintColor: INK,
      headerShadowVisible: false,
      headerLeft: () => (
        <Pressable onPress={leaveReader} style={styles.headerButton} accessibilityRole="button" accessibilityLabel="Exit book reader">
          <Ionicons name="chevron-back" size={24} color={INK} />
        </Pressable>
      ),
    });
  }, [data?.story_title_ar, data?.story_title_en, navigation, leaveReader]);

  const loadPage = useCallback(async (preferredSentenceIndex?: number, preferredTokenPosition?: number) => {
    if (!Number.isFinite(storyId) || !Number.isFinite(pageNumber)) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(false);
    setSaveError(false);
    setSelectedToken(null);
    setLookup(null);
    try {
      const [next, rawLocation, savedSpan, savedPolicy, legacyMode] = await Promise.all([
        getBookPageDetail(storyId, pageNumber),
        AsyncStorage.getItem(bookReaderLocationKey(storyId)).catch(() => null),
        AsyncStorage.getItem(SPAN_KEY).catch(() => null),
        AsyncStorage.getItem(POLICY_KEY).catch(() => null),
        AsyncStorage.getItem(LEGACY_MODE_KEY).catch(() => null),
      ]);
      const resolvedPolicy: BookReaderPolicy = savedPolicy === "guided" ? "guided" : "clean";
      const savedMode = await AsyncStorage.getItem(bookReaderModeKey(resolvedPolicy)).catch(() => null);
      const resolvedMode: BookReaderMode = savedMode === "arabic" || savedMode === "both"
        ? savedMode
        : resolvedPolicy === "guided"
          ? "arabic"
          : legacyMode === "arabic" || legacyMode === "both"
            ? legacyMode
            : "both";
      const localLocation = parseBookReaderLocation(rawLocation);
      const desiredIndex = preferredSentenceIndex
        ?? (params.atEnd === "1" ? next.passages.at(-1)?.sentence_index : undefined)
        ?? (localLocation?.pageNumber === pageNumber ? localLocation.sentenceIndex : undefined)
        ?? next.resume_sentence_index
        ?? next.passages[0]?.sentence_index;
      const desiredTokenPosition = preferredTokenPosition
        ?? (params.atEnd === "1" ? next.passages.at(-1)?.token_positions[0] : undefined)
        ?? (localLocation?.pageNumber === pageNumber ? localLocation.tokenPosition : undefined)
        ?? next.resume_token_position;
      const desiredOffset = Math.max(
        0,
        next.passages.findIndex((passage) =>
          desiredTokenPosition != null
            ? passage.token_positions.includes(desiredTokenPosition)
            : passage.sentence_index === desiredIndex
        ),
      );
      setData(next);
      setOffset(desiredOffset);
      setMode(resolvedMode);
      setPolicy(resolvedPolicy);
      if (savedSpan === "2") setSpan(2);
      else if (savedSpan === "1") setSpan(1);
      passageStartedAt.current = Date.now();
    } catch (error) {
      console.error("Failed to load book passage", error);
      setData(null);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [pageNumber, params.atEnd, storyId]);

  useEffect(() => {
    loadPage();
  }, [loadPage]);

  useEffect(() => {
    if (!data) return;
    AsyncStorage.setItem(ACTIVE_BOOK_READER_KEY, JSON.stringify({
      storyId: data.story_id,
      pageNumber: data.page_number,
      active: true,
    })).catch(() => {});
  }, [data]);

  const visiblePassages = useMemo(
    () => data?.passages.slice(offset, offset + span) ?? [],
    [data?.passages, offset, span],
  );
  const visibleSentenceIndices = useMemo(
    () => Array.from(new Set(visiblePassages.flatMap((passage) => passage.sentence_indices))),
    [visiblePassages],
  );
  const visiblePositionList = useMemo(
    () => visiblePassages.flatMap((passage) => passage.token_positions),
    [visiblePassages],
  );
  const visiblePositions = useMemo(
    () => new Set(visiblePositionList),
    [visiblePositionList],
  );
  const visibleTokens = useMemo(
    () => data?.tokens.filter((token) => visiblePositions.has(token.position)) ?? [],
    [data?.tokens, visiblePositions],
  );

  useEffect(() => {
    if (!data || visibleSentenceIndices.length === 0) return;
    let cancelled = false;
    setDraftReady(false);
    setSelectedToken(null);
    setLookup(null);
    const key = bookPassageDraftKey(data.story_id, data.page_number, visiblePositionList, policy);
    AsyncStorage.getItem(key)
      .then((raw) => {
        if (cancelled) return;
        setDraft(parseBookPassageDraft(raw) ?? emptyDraft(data.story_id, data.page_number, visiblePositionList, policy));
        passageStartedAt.current = Date.now();
        setDraftReady(true);
      })
      .catch(() => {
        if (cancelled) return;
        setDraft(emptyDraft(data.story_id, data.page_number, visiblePositionList, policy));
        setDraftReady(true);
      });
    return () => { cancelled = true; };
  }, [data, policy, visiblePositionList, visibleSentenceIndices]);

  useEffect(() => {
    if (!data || !draft || !draftReady || visibleSentenceIndices.length === 0) return;
    const key = bookPassageDraftKey(data.story_id, data.page_number, visiblePositionList, policy);
    AsyncStorage.setItem(key, JSON.stringify(draft)).catch((error) =>
      console.warn("Failed to persist passage marks", error),
    );
  }, [data, draft, draftReady, policy, visiblePositionList, visibleSentenceIndices]);

  useEffect(() => {
    const currentPassage = data?.passages[offset];
    const sentenceIndex = currentPassage?.sentence_index;
    if (!data || sentenceIndex == null) return;
    const { story_id: currentStoryId, page_number: currentPageNumber } = data;
    AsyncStorage.setItem(
      bookReaderLocationKey(currentStoryId),
      JSON.stringify({
        pageNumber: currentPageNumber,
        sentenceIndex,
        tokenPosition: currentPassage?.token_positions[0],
      }),
    ).catch(() => {});
  }, [data, offset]);

  function updateMode(next: BookReaderMode) {
    modeRequest.current += 1;
    setMode(next);
    AsyncStorage.setItem(bookReaderModeKey(policy), next).catch(() => {});
  }

  function updateSpan(next: 1 | 2) {
    setSpan(next);
    AsyncStorage.setItem(SPAN_KEY, String(next)).catch(() => {});
  }

  function updatePolicy(next: BookReaderPolicy) {
    const requestId = ++modeRequest.current;
    setPolicy(next);
    const fallback: BookReaderMode = next === "guided" ? "arabic" : "both";
    setMode(fallback);
    AsyncStorage.getItem(bookReaderModeKey(next))
      .then((saved) => {
        if (requestId === modeRequest.current && (saved === "arabic" || saved === "both")) {
          setMode(saved);
        }
      })
      .catch(() => {});
    AsyncStorage.setItem(POLICY_KEY, next).catch(() => {});
  }

  function closeToken() {
    lookupRequest.current += 1;
    setSelectedToken(null);
    setLookup(null);
    setLookupLoading(false);
    setLookupError(false);
  }

  function toggleGuidedLearning(token: BookPageToken) {
    const positions = positionsForSameGuidedWord(visibleTokens, token);
    setDraft((current) => {
      if (!current) return current;
      const alreadySelected = positions.some((position) => (
        current.learnTokenPositions.includes(position)
      ));
      return {
        ...current,
        learnTokenPositions: alreadySelected
          ? current.learnTokenPositions.filter((position) => !positions.includes(position))
          : Array.from(new Set([...current.learnTokenPositions, ...positions])),
      };
    });
  }

  async function openToken(token: BookPageToken) {
    if (!draft || !token.is_schedulable) return;
    if (sameBookToken(selectedToken, token)) {
      closeToken();
      return;
    }
    const requestId = ++lookupRequest.current;
    setSelectedToken(token);
    setLookup(null);
    setLookupError(false);
    setLookupLoading(false);

    if (policy === "guided" && token.reader_gloss_eligible) {
      if (token.lemma_id != null) {
        setLookupLoading(true);
        try {
          const result = await lookupReviewWord(token.lemma_id);
          if (lookupRequest.current === requestId) setLookup(result);
        } catch {
          if (lookupRequest.current === requestId) setLookupError(true);
        } finally {
          if (lookupRequest.current === requestId) setLookupLoading(false);
        }
      }
      return;
    }

    if (token.lemma_id != null) {
      setDraft((current) => current ? {
        ...current,
        unknownLemmaIds: Array.from(new Set([...current.unknownLemmaIds, token.lemma_id!])),
        markedTokenPositions: Array.from(new Set([...current.markedTokenPositions, token.position])),
      } : current);
      setLookupLoading(true);
      try {
        const result = await lookupReviewWord(token.lemma_id);
        if (lookupRequest.current === requestId) setLookup(result);
      } catch {
        if (lookupRequest.current === requestId) setLookupError(true);
      } finally {
        if (lookupRequest.current === requestId) setLookupLoading(false);
      }
      return;
    }

    const positions = positionsForSameUnmappedSurface(visibleTokens, token);
    setDraft((current) => current ? {
      ...current,
      unknownTokenPositions: Array.from(new Set([...current.unknownTokenPositions, ...positions])),
      dontLearnTokenPositions: current.dontLearnTokenPositions.filter((position) => !positions.includes(position)),
    } : current);
  }

  function undoUnknown(token: BookPageToken) {
    if (!draft) return;
    if (token.lemma_id != null) {
      const sameLemmaPositions = new Set(
        visibleTokens
          .filter((candidate) => candidate.lemma_id === token.lemma_id)
          .map((candidate) => candidate.position),
      );
      setDraft({
        ...draft,
        unknownLemmaIds: draft.unknownLemmaIds.filter((lemmaId) => lemmaId !== token.lemma_id),
        markedTokenPositions: draft.markedTokenPositions.filter((position) => !sameLemmaPositions.has(position)),
      });
      return;
    }
    const positions = positionsForSameUnmappedSurface(visibleTokens, token);
    setDraft({
      ...draft,
      unknownTokenPositions: draft.unknownTokenPositions.filter((position) => !positions.includes(position)),
    });
  }

  function markDontLearn(token: BookPageToken) {
    if (!draft || token.lemma_id != null) return;
    const positions = positionsForSameUnmappedSurface(visibleTokens, token);
    setDraft({
      ...draft,
      unknownTokenPositions: draft.unknownTokenPositions.filter((position) => !positions.includes(position)),
      dontLearnTokenPositions: Array.from(new Set([...draft.dontLearnTokenPositions, ...positions])),
    });
  }

  function moveBackward() {
    if (offset > 0) {
      setOffset((current) => Math.max(0, current - span));
      return;
    }
    if (pageNumber > 1) {
      router.replace(`/book-page?storyId=${storyId}&page=${pageNumber - 1}&atEnd=1`);
    }
  }

  async function advance() {
    if (!data || !draft || !draftReady || submitting || visibleSentenceIndices.length === 0) return;
    setSubmitting(true);
    setSaveError(false);
    try {
      await completeBookPassage(data.story_id, data.page_number, {
        reader_policy: policy,
        sentence_indices: visibleSentenceIndices,
        passage_token_positions: visiblePositionList,
        unknown_lemma_ids: draft.unknownLemmaIds,
        unknown_token_positions: draft.unknownTokenPositions,
        dont_learn_token_positions: policy === "clean" ? draft.dontLearnTokenPositions : [],
        learn_token_positions: policy === "guided" ? draft.learnTokenPositions : [],
        reading_time_ms: Date.now() - passageStartedAt.current,
        client_review_id: draft.clientReviewId,
      });
      await AsyncStorage.removeItem(
        bookPassageDraftKey(data.story_id, data.page_number, visiblePositionList, policy),
      ).catch(() => {});

      const nextOffset = offset + visiblePassages.length;
      if (nextOffset < data.passages.length) {
        const nextSentenceIndex = data.passages[nextOffset].sentence_index;
        const nextTokenPosition = data.passages[nextOffset].token_positions[0];
        await AsyncStorage.setItem(
          bookReaderLocationKey(data.story_id),
          JSON.stringify({
            pageNumber: data.page_number,
            sentenceIndex: nextSentenceIndex,
            tokenPosition: nextTokenPosition,
          }),
        );
        await loadPage(nextSentenceIndex, nextTokenPosition);
      } else if (data.page_number < data.page_count) {
        router.replace(`/book-page?storyId=${storyId}&page=${data.page_number + 1}`);
      } else {
        await Promise.all([
          AsyncStorage.removeItem(bookReaderLocationKey(data.story_id)).catch(() => {}),
          AsyncStorage.setItem(ACTIVE_BOOK_READER_KEY, JSON.stringify({
            storyId: data.story_id,
            pageNumber: data.page_number,
            active: false,
          })).catch(() => {}),
        ]);
        router.replace("/books");
      }
    } catch (error) {
      console.error("Failed to save book passage", error);
      setSaveError(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return <View style={styles.center}><StatusBar style="dark" /><ActivityIndicator size="large" color={ACCENT} /></View>;
  }

  if (!data || visiblePassages.length === 0) {
    return (
      <View style={styles.center}>
        <StatusBar style="dark" />
        <Ionicons name={loadError ? "cloud-offline-outline" : "book-outline"} size={36} color={MUTED} />
        <Text style={styles.errorTitle}>{loadError ? "Couldn’t open this passage" : "Nothing to read here"}</Text>
        <Text style={styles.errorCopy}>Your location and word marks are still saved.</Text>
        {loadError && <Pressable style={styles.primarySmall} onPress={() => loadPage()}><Text style={styles.primarySmallText}>Try again</Text></Pressable>}
        <Pressable style={styles.textButton} onPress={leaveReader}><Text style={styles.textButtonLabel}>Back to library</Text></Pressable>
      </View>
    );
  }

  const atBookStart = pageNumber === 1 && offset === 0;
  const atBookEnd = pageNumber === data.page_count && offset + visiblePassages.length >= data.passages.length;
  const unknownCount = (draft?.unknownLemmaIds.length ?? 0) + (draft?.unknownTokenPositions.length ?? 0);
  const translation = visiblePassages
    .map((passage) => passage.english_translation)
    .filter(Boolean)
    .join(" ");
  const selectedMarkedUnknown = selectedToken?.lemma_id != null
    ? draft?.unknownLemmaIds.includes(selectedToken.lemma_id) === true
    : selectedToken != null && draft?.unknownTokenPositions.includes(selectedToken.position) === true;
  const selectedDontLearn = selectedToken != null
    && selectedToken.lemma_id == null
    && draft?.dontLearnTokenPositions.includes(selectedToken.position) === true;
  const selectedGuided = policy === "guided"
    && selectedToken?.reader_gloss_eligible === true;
  const selectedForLearning = selectedToken != null
    && draft?.learnTokenPositions.includes(selectedToken.position) === true;
  const learningCount = countGuidedLearningWords(
    visibleTokens,
    draft?.learnTokenPositions ?? [],
  );

  return (
    <View style={styles.container}>
      <StatusBar style="dark" />
      <View style={styles.toolbar}>
        <Text style={styles.locationLabel}>
          {data.source_page_number != null ? data.source_page_number : data.page_number}
          <Text style={styles.locationTotal}> · {data.page_number}/{data.page_count}</Text>
        </Text>
        <View style={styles.controls}>
          <Pressable
            style={[styles.compactControl, policy === "guided" && styles.compactControlActive]}
            onPress={() => updatePolicy(policy === "guided" ? "clean" : "guided")}
            accessibilityRole="button"
            accessibilityLabel={`Reading help: ${policy}. Tap for ${policy === "guided" ? "clean" : "guided"}`}
          >
            <Ionicons name={policy === "guided" ? "sparkles" : "sparkles-outline"} size={13} color={policy === "guided" ? "#FFF9ED" : MUTED} />
            <Text style={[styles.compactControlText, policy === "guided" && styles.compactControlTextActive]}>{policy === "guided" ? "Guided" : "Clean"}</Text>
          </Pressable>
          <Pressable
            style={styles.squareControl}
            onPress={() => updateSpan(span === 1 ? 2 : 1)}
            accessibilityRole="button"
            accessibilityLabel={`Showing ${span} ${span === 1 ? "passage" : "passages"}. Tap to show ${span === 1 ? 2 : 1}`}
          >
            <Text style={styles.squareControlText}>{span}</Text>
          </Pressable>
          <Pressable
            style={[styles.squareControl, mode === "both" && styles.compactControlActive]}
            onPress={() => updateMode(mode === "both" ? "arabic" : "both")}
            accessibilityRole="button"
            accessibilityLabel={mode === "both" ? "Hide full English translation" : "Show full English translation"}
          >
            <Text style={[styles.squareControlText, mode === "both" && styles.compactControlTextActive]}>EN</Text>
          </Pressable>
        </View>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        <View style={styles.paper}>
          {policy === "guided" ? (
            <View style={styles.guidedParagraph}>
              {visibleTokens.map((token) => {
                const marked = isBookTokenMarked(draft, token);
                const learning = draft?.learnTokenPositions.includes(token.position);
                const inlineGloss = token.reader_gloss_eligible ? shortBookGloss(token.gloss_en) : null;
                const displayed = token.show_tashkeel ? token.surface_form : stripDiacritics(token.surface_form);
                return (
                  <Pressable
                    key={token.position}
                    onPress={() => openToken(token)}
                    accessibilityRole="button"
                    accessibilityLabel={`${token.surface_form}${inlineGloss ? `, ${inlineGloss}. Tap to ${learning ? "stop learning" : "learn"}` : ""}`}
                    style={[
                      styles.guidedToken,
                      marked && styles.guidedTokenUnknown,
                      learning && styles.guidedTokenLearning,
                      selectedToken?.position === token.position && styles.guidedTokenSelected,
                    ]}
                  >
                    <Text style={[styles.guidedArabic, marked && styles.arabicUnknownText, learning && styles.arabicLearning]}>{displayed}</Text>
                    {inlineGloss && (
                      <Text numberOfLines={1} style={[styles.microGloss, learning && styles.inlineGlossLearning]}>{inlineGloss}</Text>
                    )}
                  </Pressable>
                );
              })}
            </View>
          ) : (
            <Text style={styles.arabicParagraph} selectable>
              {visibleTokens.map((token, index) => {
              const marked = isBookTokenMarked(draft, token);
              const ignored = draft?.dontLearnTokenPositions.includes(token.position);
              const displayed = token.show_tashkeel ? token.surface_form : stripDiacritics(token.surface_form);
              return (
                <Text key={token.position}>
                  <Text
                    onPress={() => openToken(token)}
                    accessibilityRole="button"
                    accessibilityLabel={`${token.surface_form}${token.gloss_en ? `, ${token.gloss_en}` : ""}`}
                    style={[
                      styles.arabicWord,
                      marked && styles.arabicUnknown,
                      ignored && styles.arabicIgnored,
                      selectedToken?.position === token.position && styles.arabicSelected,
                    ]}
                  >
                    {displayed}
                  </Text>
                  {index < visibleTokens.length - 1 ? " " : ""}
                </Text>
              );
            })}
            </Text>
          )}

          {mode === "both" && (
            <View style={styles.translationBlock}>
              <Text style={styles.translationLabel}>ENGLISH</Text>
              <Text style={styles.translation}>{translation || "Translation unavailable for this passage."}</Text>
            </View>
          )}
        </View>
      </ScrollView>

      {selectedToken && (
        <View style={styles.lookupPanel}>
          <Pressable style={styles.lookupClose} onPress={closeToken} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close word details">
            <Ionicons name="close" size={19} color={MUTED} />
          </Pressable>
          <View style={styles.lookupTitleRow}>
            <Text style={styles.lookupArabic}>{lookup?.lemma_ar || selectedToken.surface_form}</Text>
            {lookupLoading && <ActivityIndicator size="small" color={ACCENT} />}
          </View>
          <View style={styles.lookupSummary}>
            <Text style={styles.lookupGloss}>{lookup?.gloss_en || selectedToken.gloss_en || "Translation unavailable"}</Text>
            <Text style={styles.lookupStatus}>
              {selectedGuided
                ? selectedForLearning ? "Will learn" : "Not tracked"
                : selectedToken.lemma_id == null ? "New word" : "Marked unknown"}
            </Text>
          </View>
          {lookupError && <Text style={styles.lookupError}>Full entry unavailable right now. Your mark is still saved.</Text>}
          <View style={styles.lookupActions}>
            {selectedGuided ? (
              <Pressable style={[styles.secondaryAction, selectedForLearning && styles.learningAction]} onPress={() => toggleGuidedLearning(selectedToken)}>
                <Text style={[styles.secondaryActionText, selectedForLearning && styles.learningActionText]}>
                  {selectedForLearning ? "Learning · Undo" : "Learn"}
                </Text>
              </Pressable>
            ) : <>
              {selectedMarkedUnknown && (
                <Pressable style={styles.secondaryAction} onPress={() => undoUnknown(selectedToken)}>
                  <Text style={styles.secondaryActionText}>I knew this · undo</Text>
                </Pressable>
              )}
              {selectedToken.lemma_id == null && !selectedDontLearn && (
                <Pressable style={styles.secondaryAction} onPress={() => markDontLearn(selectedToken)}>
                  <Text style={styles.secondaryActionText}>Don’t learn</Text>
                </Pressable>
              )}
              {selectedDontLearn && (
                <Pressable style={styles.secondaryAction} onPress={() => openToken(selectedToken)}>
                  <Text style={styles.secondaryActionText}>Learn after all</Text>
                </Pressable>
              )}
            </>}
            {selectedToken.lemma_id != null && (
              <Pressable style={styles.entryAction} onPress={() => router.push(`/word/${selectedToken.lemma_id}`)}>
                <Text style={styles.entryActionText}>Full entry</Text>
                <Ionicons name="arrow-forward" size={15} color={ACCENT} />
              </Pressable>
            )}
          </View>
        </View>
      )}

      {saveError && (
        <View style={styles.saveError} accessibilityRole="alert">
          <Ionicons name="cloud-offline-outline" size={17} color={UNKNOWN} />
          <Text style={styles.saveErrorText}>Couldn’t save. Nothing advanced; your marks are safe.</Text>
        </View>
      )}

      <View style={styles.footer}>
        <Pressable disabled={atBookStart || submitting} style={[styles.navButton, atBookStart && styles.disabled]} onPress={moveBackward}>
          <Ionicons name="chevron-back" size={19} color={atBookStart ? RULE : INK} />
          <Text style={[styles.navButtonText, atBookStart && styles.disabledText]}>Previous</Text>
        </Pressable>
        <View style={styles.footerStatus}>
          {(unknownCount > 0 || learningCount > 0) && (
            <Text style={styles.footerStatusText}>
              {unknownCount > 0 ? `${unknownCount} unknown` : ""}
              {unknownCount > 0 && learningCount > 0 ? " · " : ""}
              {learningCount > 0 ? `${learningCount} learning` : ""}
            </Text>
          )}
        </View>
        <Pressable disabled={submitting || !draftReady} style={[styles.nextButton, (!draftReady || submitting) && styles.disabled]} onPress={advance}>
          {submitting ? <ActivityIndicator size="small" color="#FFF9ED" /> : <>
            <Text style={styles.nextButtonText}>{atBookEnd ? "Finish" : "Next"}</Text>
            <Ionicons name={atBookEnd ? "checkmark" : "chevron-forward"} size={19} color="#FFF9ED" />
          </>}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: PAPER },
  center: { flex: 1, backgroundColor: PAPER, alignItems: "center", justifyContent: "center", padding: 28 },
  headerButton: { paddingHorizontal: 12, paddingVertical: 6 },
  toolbar: { minHeight: 42, borderBottomWidth: 1, borderBottomColor: RULE + "99", paddingHorizontal: 18, paddingVertical: 6, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  locationLabel: { color: ACCENT, fontSize: 13, fontWeight: "800", fontVariant: ["tabular-nums"] },
  locationTotal: { color: MUTED, fontWeight: "500" },
  controls: { flexDirection: "row", alignItems: "center", gap: 6 },
  compactControl: { height: 30, paddingHorizontal: 10, borderRadius: 15, borderCurve: "continuous", backgroundColor: PAPER_DEEP + "88", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5 },
  compactControlActive: { backgroundColor: INK },
  compactControlText: { color: MUTED, fontSize: 11, fontWeight: "700" },
  compactControlTextActive: { color: "#FFF9ED" },
  squareControl: { width: 34, height: 30, borderRadius: 15, borderCurve: "continuous", backgroundColor: PAPER_DEEP + "88", alignItems: "center", justifyContent: "center" },
  squareControlText: { color: MUTED, fontSize: 11, fontWeight: "800", fontVariant: ["tabular-nums"] },
  scroll: { flex: 1 },
  scrollContent: { paddingHorizontal: 20, paddingTop: 16, paddingBottom: 30, alignItems: "center" },
  paper: { width: "100%", maxWidth: 720, paddingHorizontal: 4, paddingVertical: 8 },
  arabicParagraph: { color: INK, fontFamily: fontFamily.arabicNoto, fontSize: 22, lineHeight: 39, writingDirection: "rtl", textAlign: "right" },
  arabicWord: { color: INK, fontFamily: fontFamily.arabicNoto },
  arabicUnknown: { color: UNKNOWN, backgroundColor: "#D9968430" },
  arabicUnknownText: { color: UNKNOWN },
  arabicIgnored: { color: MUTED, textDecorationLine: "line-through" },
  arabicLearning: { color: ACCENT, textDecorationLine: "underline" },
  arabicSelected: { backgroundColor: "#C98D5638" },
  guidedParagraph: { width: "100%", flexDirection: "row-reverse", flexWrap: "wrap", alignItems: "flex-start", justifyContent: "flex-start", columnGap: 7, rowGap: 8 },
  guidedToken: { minHeight: 39, paddingHorizontal: 2, paddingVertical: 1, borderRadius: 5, borderCurve: "continuous", alignItems: "center", justifyContent: "flex-start" },
  guidedTokenUnknown: { backgroundColor: "#D9968424" },
  guidedTokenLearning: { backgroundColor: "#C98D5618" },
  guidedTokenSelected: { backgroundColor: "#C98D5630" },
  guidedArabic: { color: INK, fontFamily: fontFamily.arabicNoto, fontSize: 22, lineHeight: 27, writingDirection: "rtl" },
  microGloss: { maxWidth: 84, color: MUTED, fontFamily: fontFamily.translitRegular, fontSize: 8.5, lineHeight: 11, textAlign: "center" },
  inlineGlossLearning: { color: ACCENT, fontWeight: "700" },
  translationBlock: { marginTop: 24, paddingTop: 18, borderTopWidth: 1, borderTopColor: RULE },
  translationLabel: { color: ACCENT, fontSize: 9, fontWeight: "800", letterSpacing: 1.2, marginBottom: 7 },
  translation: { color: "#44392D", fontFamily: "Georgia", fontSize: 15, lineHeight: 24 },
  lookupPanel: { backgroundColor: PAPER_DEEP, borderTopWidth: 1, borderTopColor: RULE, paddingHorizontal: 18, paddingTop: 14, paddingBottom: 12 },
  lookupClose: { position: "absolute", right: 14, top: 12, zIndex: 2, padding: 5 },
  lookupTitleRow: { flexDirection: "row-reverse", justifyContent: "flex-end", alignItems: "center", gap: 10, paddingRight: 30 },
  lookupArabic: { color: INK, fontFamily: fontFamily.arabicNoto, fontSize: 25, lineHeight: 38, textAlign: "right" },
  lookupSummary: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 1 },
  lookupGloss: { color: INK, fontSize: 15, fontWeight: "700" },
  lookupStatus: { color: MUTED, fontSize: 10, fontWeight: "700", paddingHorizontal: 7, paddingVertical: 3, borderRadius: 10, overflow: "hidden", backgroundColor: "#F3E8D2" },
  lookupError: { color: UNKNOWN, fontSize: 12, marginTop: 5 },
  lookupActions: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 10 },
  secondaryAction: { borderWidth: 1, borderColor: "#B99D75", borderRadius: 7, paddingHorizontal: 11, minHeight: 34, justifyContent: "center" },
  secondaryActionText: { color: INK, fontSize: 12, fontWeight: "700" },
  learningAction: { borderColor: ACCENT, backgroundColor: "#F2DDC4" },
  learningActionText: { color: ACCENT },
  entryAction: { flexDirection: "row", gap: 5, alignItems: "center", paddingHorizontal: 8, minHeight: 34 },
  entryActionText: { color: ACCENT, fontSize: 12, fontWeight: "800" },
  saveError: { flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: "#F3D8CE", borderTopWidth: 1, borderTopColor: "#D7AA99", paddingHorizontal: 16, paddingVertical: 9 },
  saveErrorText: { color: "#71372F", fontSize: 12, flex: 1 },
  footer: { minHeight: 52, paddingHorizontal: 12, paddingVertical: 6, borderTopWidth: 1, borderTopColor: RULE, backgroundColor: PAPER_DEEP, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  navButton: { minWidth: 82, minHeight: 38, paddingHorizontal: 8, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 2, borderRadius: 19, borderCurve: "continuous" },
  navButtonText: { color: INK, fontSize: 13, fontWeight: "700" },
  footerStatus: { flex: 1, alignItems: "center" },
  footerStatusText: { color: MUTED, fontSize: 11 },
  nextButton: { minWidth: 82, minHeight: 38, paddingHorizontal: 13, borderRadius: 19, borderCurve: "continuous", backgroundColor: ACCENT, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 2 },
  nextButtonText: { color: "#FFF9ED", fontSize: 13, fontWeight: "800" },
  disabled: { opacity: 0.5 },
  disabledText: { color: RULE },
  errorTitle: { color: INK, fontSize: 19, fontWeight: "800", marginTop: 13 },
  errorCopy: { color: MUTED, fontSize: 13, marginTop: 6, textAlign: "center" },
  primarySmall: { backgroundColor: ACCENT, paddingHorizontal: 20, minHeight: 42, borderRadius: 8, justifyContent: "center", marginTop: 16 },
  primarySmallText: { color: "#FFF9ED", fontWeight: "800" },
  textButton: { padding: 12, marginTop: 3 },
  textButtonLabel: { color: ACCENT, fontWeight: "700" },
});
