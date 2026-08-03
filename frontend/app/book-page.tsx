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

import { completeBookPassage, getBookPageDetail, lookupReviewWord } from "../lib/api";
import {
  BookPassageDraft,
  BookReaderMode,
  BookReaderPolicy,
  bookPassageDraftKey,
  bookReaderLocationKey,
  countGuidedLearningWords,
  parseBookPassageDraft,
  parseBookReaderLocation,
  positionsForSameGuidedWord,
  positionsForSameUnmappedSurface,
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
const MODE_KEY = "@alif:book-reader:mode";
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
  const passageStartedAt = useRef(Date.now());

  useLayoutEffect(() => {
    navigation.setOptions({
      title: data?.story_title_en || data?.story_title_ar || "Reader",
      headerStyle: { backgroundColor: PAPER },
      headerTintColor: INK,
      headerShadowVisible: false,
      headerLeft: () => (
        <Pressable onPress={() => router.replace("/books")} style={styles.headerButton}>
          <Ionicons name="chevron-back" size={24} color={INK} />
        </Pressable>
      ),
    });
  }, [data?.story_title_ar, data?.story_title_en, navigation, router]);

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
      const [next, rawLocation, savedMode, savedSpan, savedPolicy] = await Promise.all([
        getBookPageDetail(storyId, pageNumber),
        AsyncStorage.getItem(bookReaderLocationKey(storyId)).catch(() => null),
        AsyncStorage.getItem(MODE_KEY).catch(() => null),
        AsyncStorage.getItem(SPAN_KEY).catch(() => null),
        AsyncStorage.getItem(POLICY_KEY).catch(() => null),
      ]);
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
      if (savedMode === "arabic" || savedMode === "both") setMode(savedMode);
      if (savedSpan === "2") setSpan(2);
      else if (savedSpan === "1") setSpan(1);
      if (savedPolicy === "guided" || savedPolicy === "clean") setPolicy(savedPolicy);
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
    setMode(next);
    AsyncStorage.setItem(MODE_KEY, next).catch(() => {});
  }

  function updateSpan(next: 1 | 2) {
    setSpan(next);
    AsyncStorage.setItem(SPAN_KEY, String(next)).catch(() => {});
  }

  function updatePolicy(next: BookReaderPolicy) {
    setPolicy(next);
    AsyncStorage.setItem(POLICY_KEY, next).catch(() => {});
  }

  async function openToken(token: BookPageToken) {
    if (!draft || !token.is_schedulable) return;
    const requestId = ++lookupRequest.current;
    setSelectedToken(token);
    setLookup(null);
    setLookupError(false);
    setLookupLoading(false);

    if (policy === "guided" && token.reader_gloss_eligible) {
      const positions = positionsForSameGuidedWord(visibleTokens, token);
      const alreadySelected = positions.some((position) => (
        draft.learnTokenPositions.includes(position)
      ));
      setDraft((current) => current ? {
        ...current,
        learnTokenPositions: alreadySelected
          ? current.learnTokenPositions.filter((position) => !positions.includes(position))
          : Array.from(new Set([...current.learnTokenPositions, ...positions])),
      } : current);
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
      setDraft({
        ...draft,
        unknownLemmaIds: draft.unknownLemmaIds.filter((lemmaId) => lemmaId !== token.lemma_id),
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
        await AsyncStorage.removeItem(bookReaderLocationKey(data.story_id)).catch(() => {});
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
    return <View style={styles.center}><ActivityIndicator size="large" color={ACCENT} /></View>;
  }

  if (!data || visiblePassages.length === 0) {
    return (
      <View style={styles.center}>
        <Ionicons name={loadError ? "cloud-offline-outline" : "book-outline"} size={36} color={MUTED} />
        <Text style={styles.errorTitle}>{loadError ? "Couldn’t open this passage" : "Nothing to read here"}</Text>
        <Text style={styles.errorCopy}>Your location and word marks are still saved.</Text>
        {loadError && <Pressable style={styles.primarySmall} onPress={() => loadPage()}><Text style={styles.primarySmallText}>Try again</Text></Pressable>}
        <Pressable style={styles.textButton} onPress={() => router.replace("/books")}><Text style={styles.textButtonLabel}>Back to library</Text></Pressable>
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
      <View style={styles.toolbar}>
        <View>
          <Text style={styles.pageKicker}>
            {data.source_page_number != null ? `PRINTED PAGE ${data.source_page_number}` : `PAGE ${data.page_number}`}
          </Text>
          <Text style={styles.locationLabel}>{data.page_number} / {data.page_count}</Text>
        </View>
        <View style={styles.controls}>
          <View style={styles.segmented} accessibilityLabel="Reading language">
            <Pressable style={[styles.segment, mode === "arabic" && styles.segmentActive]} onPress={() => updateMode("arabic")}>
              <Text style={[styles.segmentText, mode === "arabic" && styles.segmentTextActive]}>Arabic</Text>
            </Pressable>
            <Pressable style={[styles.segment, mode === "both" && styles.segmentActive]} onPress={() => updateMode("both")}>
              <Text style={[styles.segmentText, mode === "both" && styles.segmentTextActive]}>Both</Text>
            </Pressable>
          </View>
          <View style={styles.segmented} accessibilityLabel="Passage length">
            <Pressable style={[styles.segmentNarrow, span === 1 && styles.segmentActive]} onPress={() => updateSpan(1)}>
              <Text style={[styles.segmentText, span === 1 && styles.segmentTextActive]}>1</Text>
            </Pressable>
            <Pressable style={[styles.segmentNarrow, span === 2 && styles.segmentActive]} onPress={() => updateSpan(2)}>
              <Text style={[styles.segmentText, span === 2 && styles.segmentTextActive]}>2</Text>
            </Pressable>
          </View>
        </View>
      </View>
      <View style={styles.helpBar}>
        <View style={styles.helpLabelRow}>
          <Ionicons name="sparkles-outline" size={14} color={ACCENT} />
          <Text style={styles.helpLabel}>READING HELP</Text>
        </View>
        <View style={styles.segmented} accessibilityLabel="Reading help">
          <Pressable style={[styles.helpSegment, policy === "clean" && styles.segmentActive]} onPress={() => updatePolicy("clean")}>
            <Text style={[styles.segmentText, policy === "clean" && styles.segmentTextActive]}>Clean</Text>
          </Pressable>
          <Pressable style={[styles.helpSegment, policy === "guided" && styles.segmentActive]} onPress={() => updatePolicy("guided")}>
            <Text style={[styles.segmentText, policy === "guided" && styles.segmentTextActive]}>Guided</Text>
          </Pressable>
        </View>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        <View style={styles.paper}>
          <Text style={styles.arabicParagraph} selectable>
            {visibleTokens.map((token, index) => {
              const marked = token.lemma_id != null
                ? draft?.unknownLemmaIds.includes(token.lemma_id)
                : draft?.unknownTokenPositions.includes(token.position);
              const ignored = draft?.dontLearnTokenPositions.includes(token.position);
              const learning = draft?.learnTokenPositions.includes(token.position);
              const inlineGloss = policy === "guided" && token.reader_gloss_eligible
                ? shortBookGloss(token.gloss_en)
                : null;
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
                      learning && styles.arabicLearning,
                      selectedToken?.position === token.position && styles.arabicSelected,
                    ]}
                  >
                    {displayed}
                  </Text>
                  {inlineGloss && (
                    <Text
                      onPress={() => openToken(token)}
                      accessibilityRole="button"
                      accessibilityLabel={`${inlineGloss}. Tap to ${learning ? "stop learning" : "learn"} ${token.surface_form}`}
                      style={[styles.inlineGloss, learning && styles.inlineGlossLearning]}
                    >
                      {` ‹⁦${inlineGloss}⁩›`}
                    </Text>
                  )}
                  {index < visibleTokens.length - 1 ? " " : ""}
                </Text>
              );
            })}
          </Text>

          {mode === "both" && (
            <View style={styles.translationBlock}>
              <Text style={styles.translationLabel}>ENGLISH</Text>
              <Text style={styles.translation}>{translation || "Translation unavailable for this passage."}</Text>
            </View>
          )}
        </View>
        <Text style={styles.tapHint}>
          {policy === "guided"
            ? "Small glosses are free help and are not tracked. Tap one only when you want to learn it."
            : "Tap a word you didn’t know. Untapped words are recorded only when you continue."}
        </Text>
      </ScrollView>

      {selectedToken && (
        <View style={styles.lookupPanel}>
          <Pressable style={styles.lookupClose} onPress={() => { lookupRequest.current += 1; setSelectedToken(null); }} hitSlop={10}>
            <Ionicons name="close" size={19} color={MUTED} />
          </Pressable>
          <View style={styles.lookupTitleRow}>
            <Text style={styles.lookupArabic}>{lookup?.lemma_ar || selectedToken.surface_form}</Text>
            {lookupLoading && <ActivityIndicator size="small" color={ACCENT} />}
          </View>
          <Text style={styles.lookupGloss}>{lookup?.gloss_en || selectedToken.gloss_en || "Translation not available yet"}</Text>
          {selectedGuided ? (
            <Text style={styles.lookupExplanation}>
              Guided word · this gloss is free help and is not tracked unless you choose to learn it.
            </Text>
          ) : selectedToken.lemma_id == null ? (
            <Text style={styles.lookupExplanation}>New word · a full entry will be built only if this passage admits it.</Text>
          ) : (
            <Text style={styles.lookupExplanation}>
              Existing word · this will count as a miss in your normal review history.
            </Text>
          )}
          {lookupError && <Text style={styles.lookupError}>Full entry unavailable right now. Your mark is still saved.</Text>}
          <View style={styles.lookupActions}>
            {selectedGuided ? (
              <Pressable style={[styles.secondaryAction, selectedForLearning && styles.learningAction]} onPress={() => openToken(selectedToken)}>
                <Text style={[styles.secondaryActionText, selectedForLearning && styles.learningActionText]}>
                  {selectedForLearning ? "Learning on Next · undo" : "Learn this word"}
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
          <Text style={styles.footerStatusText}>
            {unknownCount > 0
              ? `${unknownCount} unknown${learningCount > 0 ? ` · ${learningCount} to learn` : ""}`
              : learningCount > 0
                ? `${learningCount} to learn`
                : `${visiblePassages.length} passage${visiblePassages.length > 1 ? "s" : ""}`}
          </Text>
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
  toolbar: { borderTopWidth: 1, borderBottomWidth: 1, borderColor: RULE, paddingHorizontal: 18, paddingVertical: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  pageKicker: { color: ACCENT, fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  locationLabel: { color: MUTED, fontSize: 11, marginTop: 2 },
  controls: { flexDirection: "row", alignItems: "center", gap: 8 },
  helpBar: { minHeight: 42, borderBottomWidth: 1, borderBottomColor: RULE, paddingHorizontal: 18, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  helpLabelRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  helpLabel: { color: ACCENT, fontSize: 9, fontWeight: "800", letterSpacing: 1.1 },
  segmented: { flexDirection: "row", borderWidth: 1, borderColor: RULE, borderRadius: 8, overflow: "hidden" },
  segment: { paddingHorizontal: 10, minHeight: 32, justifyContent: "center" },
  segmentNarrow: { width: 34, minHeight: 32, alignItems: "center", justifyContent: "center" },
  helpSegment: { minWidth: 68, minHeight: 28, paddingHorizontal: 10, alignItems: "center", justifyContent: "center" },
  segmentActive: { backgroundColor: INK },
  segmentText: { color: MUTED, fontSize: 12, fontWeight: "700" },
  segmentTextActive: { color: "#FFF9ED" },
  scroll: { flex: 1 },
  scrollContent: { paddingHorizontal: 16, paddingTop: 18, paddingBottom: 34, alignItems: "center" },
  paper: { width: "100%", maxWidth: 720, paddingHorizontal: 18, paddingVertical: 22, backgroundColor: "#F8EEDB", borderWidth: 1, borderColor: RULE, borderRadius: 8, boxShadow: "0 4px 12px rgba(78, 56, 36, 0.08)" },
  arabicParagraph: { color: INK, fontFamily: fontFamily.arabicNoto, fontSize: 23, lineHeight: 42, writingDirection: "rtl", textAlign: "right" },
  arabicWord: { color: INK, fontFamily: fontFamily.arabicNoto },
  arabicUnknown: { color: UNKNOWN, backgroundColor: "#D9968430" },
  arabicIgnored: { color: MUTED, textDecorationLine: "line-through" },
  arabicLearning: { color: ACCENT, textDecorationLine: "underline" },
  arabicSelected: { backgroundColor: "#C98D5638" },
  inlineGloss: { color: MUTED, fontFamily: "Georgia", fontSize: 9, fontStyle: "italic" },
  inlineGlossLearning: { color: ACCENT, fontWeight: "700" },
  translationBlock: { marginTop: 22, paddingTop: 18, borderTopWidth: 1, borderTopColor: RULE },
  translationLabel: { color: ACCENT, fontSize: 10, fontWeight: "800", letterSpacing: 1.3, marginBottom: 8 },
  translation: { color: "#44392D", fontFamily: "Georgia", fontSize: 16, lineHeight: 25 },
  tapHint: { color: MUTED, fontSize: 12, lineHeight: 18, textAlign: "center", marginTop: 14, maxWidth: 360 },
  lookupPanel: { backgroundColor: PAPER_DEEP, borderTopWidth: 1, borderTopColor: RULE, paddingHorizontal: 18, paddingTop: 14, paddingBottom: 12 },
  lookupClose: { position: "absolute", right: 14, top: 12, zIndex: 2, padding: 5 },
  lookupTitleRow: { flexDirection: "row-reverse", justifyContent: "flex-end", alignItems: "center", gap: 10, paddingRight: 30 },
  lookupArabic: { color: INK, fontFamily: fontFamily.arabicNoto, fontSize: 25, lineHeight: 38, textAlign: "right" },
  lookupGloss: { color: INK, fontSize: 16, fontWeight: "700", marginTop: 1 },
  lookupExplanation: { color: MUTED, fontSize: 12, lineHeight: 17, marginTop: 4 },
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
  footer: { minHeight: 64, paddingHorizontal: 12, paddingVertical: 9, borderTopWidth: 1, borderTopColor: RULE, backgroundColor: PAPER_DEEP, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  navButton: { minWidth: 96, minHeight: 44, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 3, borderRadius: 8 },
  navButtonText: { color: INK, fontSize: 14, fontWeight: "700" },
  footerStatus: { flex: 1, alignItems: "center" },
  footerStatusText: { color: MUTED, fontSize: 11 },
  nextButton: { minWidth: 96, minHeight: 44, paddingHorizontal: 15, borderRadius: 8, backgroundColor: ACCENT, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 3 },
  nextButtonText: { color: "#FFF9ED", fontSize: 14, fontWeight: "800" },
  disabled: { opacity: 0.5 },
  disabledText: { color: RULE },
  errorTitle: { color: INK, fontSize: 19, fontWeight: "800", marginTop: 13 },
  errorCopy: { color: MUTED, fontSize: 13, marginTop: 6, textAlign: "center" },
  primarySmall: { backgroundColor: ACCENT, paddingHorizontal: 20, minHeight: 42, borderRadius: 8, justifyContent: "center", marginTop: 16 },
  primarySmallText: { color: "#FFF9ED", fontWeight: "800" },
  textButton: { padding: 12, marginTop: 3 },
  textButtonLabel: { color: ACCENT, fontWeight: "700" },
});
