"use strict";

const form = document.querySelector("#analysis-form");
const profileInput = document.querySelector("#profile");
const tagInput = document.querySelector("#tag");
const monthInput = document.querySelector("#month");
const narrativeInput = document.querySelector("#include-narrative");
const submitButton = document.querySelector("#submit-button");
const statusRegion = document.querySelector("#status-region");
const results = document.querySelector("#results");
const rawJson = document.querySelector("#raw-json");

let displayedPayload = {};

function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function decodeEntities(value) {
  const parsed = new DOMParser().parseFromString(String(value), "text/html");
  return parsed.documentElement.textContent || String(value);
}

function setDefaultMonth() {
  const now = new Date();
  monthInput.max = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  monthInput.value = "2020-08";
}

function parseUserId(value) {
  const trimmed = value.trim();
  if (/^[1-9]\d*$/.test(trimmed)) {
    const userId = Number(trimmed);
    if (Number.isSafeInteger(userId)) return userId;
  }

  let url;
  try {
    url = new URL(trimmed);
  } catch {
    throw new Error("Enter a Stack Overflow profile URL or a positive numeric user ID.");
  }
  const hostname = url.hostname.toLowerCase();
  if (hostname !== "stackoverflow.com" && hostname !== "www.stackoverflow.com") {
    throw new Error("The profile URL must be hosted on stackoverflow.com.");
  }
  const match = url.pathname.match(/^\/users\/([1-9]\d*)(?:\/|$)/);
  if (!match) throw new Error("The URL does not contain a valid Stack Overflow user ID.");
  const userId = Number(match[1]);
  if (!Number.isSafeInteger(userId)) throw new Error("The Stack Overflow user ID is too large.");
  return userId;
}

function parseTag(value) {
  const tag = value.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9.+#-]{0,34}$/.test(tag)) {
    throw new Error("Use a valid Stack Overflow tag containing at most 35 characters.");
  }
  return tag;
}

function monthRange(value) {
  const match = value.match(/^(\d{4})-(\d{2})$/);
  if (!match) throw new Error("Choose a contribution month.");
  const year = Number(match[1]);
  const monthIndex = Number(match[2]) - 1;
  const start = new Date(Date.UTC(year, monthIndex, 1));
  const end = new Date(Date.UTC(year, monthIndex + 1, 1));
  const datePart = (date) => date.toISOString().slice(0, 10);
  return { startDate: datePart(start), endDate: datePart(end) };
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("; ")
      : detail || `Request failed with HTTP ${response.status}.`;
    const error = new Error(message);
    error.status = response.status;
    error.requestId = response.headers.get("x-request-id");
    throw error;
  }
  return { payload, requestId: response.headers.get("x-request-id") };
}

function showStatus(message, kind = "info") {
  statusRegion.textContent = message;
  statusRegion.className = `status-region ${kind}`;
  statusRegion.hidden = false;
}

function hideStatus() {
  statusRegion.hidden = true;
}

function formatNumber(value, digits = 0) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

function percent(value) {
  return `${formatNumber(value * 100, 1)}%`;
}

function safeProfileUrl(value) {
  try {
    const url = new URL(value);
    const validHost = url.hostname === "stackoverflow.com" || url.hostname === "www.stackoverflow.com";
    if (url.protocol === "https:" && validHost) return url.href;
  } catch {
    return null;
  }
  return null;
}

function metricCard(label, value, note = "") {
  const card = element("div", "metric-card");
  card.append(element("span", "", label), element("strong", "", value), element("small", "", note));
  return card;
}

function benchmarkRank(value) {
  return value === null ? "Not ranked" : `#${value}`;
}

function renderMetrics(analysis) {
  const contributor = analysis.contributor;
  const hasAnswers = contributor.has_qualifying_answers;
  const cards = document.querySelector("#metric-cards");
  cards.replaceChildren(
    metricCard("Benchmark rank", benchmarkRank(contributor.period_benchmark_rank), hasAnswers ? `of ${analysis.cohort.comparison_cohort_size}` : "No qualifying answers"),
    metricCard("Official rank", contributor.official_all_time_rank ? `#${contributor.official_all_time_rank}` : "Outside", contributor.is_official_all_time_top_20 ? "All-time Top-20" : "Added subject"),
    metricCard("Answers", formatNumber(contributor.answer_count), `${contributor.accepted_answer_count} accepted`),
    metricCard("Total score", formatNumber(contributor.total_answer_score), "Native answer votes"),
    metricCard("Acceptance", hasAnswers ? percent(contributor.acceptance_rate) : "N/A", "Accepted / answers"),
    metricCard("Average score", hasAnswers ? formatNumber(contributor.average_answer_score, 2) : "N/A", "Score / answer"),
  );
}

function deltaClass(value) {
  if (value > 0) return "delta-positive";
  if (value < 0) return "delta-negative";
  return "delta-neutral";
}

function signed(value, suffix = "") {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value, 2)}${suffix}`;
}

function comparisonRow(label, value, className = "") {
  const row = element("div", "comparison-row");
  row.append(element("span", "", label), element("strong", className, value));
  return row;
}

function renderPeers(analysis) {
  const peer = analysis.peer_comparison;
  const host = document.querySelector("#peer-comparison");
  const peerRows = [
    ["Answer count", analysis.contributor.answer_count, peer.answer_count, false],
    ["Total answer score", analysis.contributor.total_answer_score, peer.total_answer_score, false],
    ["Acceptance rate", analysis.contributor.acceptance_rate, peer.acceptance_rate, true],
    ["Average answer score", analysis.contributor.average_answer_score, peer.average_answer_score, false],
  ];
  document.querySelector("#peer-note").textContent = `${peer.peer_count} active peers`;
  if (!analysis.contributor.has_qualifying_answers) {
    host.replaceChildren(element("p", "delta-neutral", "Peer percentage comparisons are omitted because the contributor had no qualifying answers."));
    return;
  }
  host.replaceChildren(...peerRows.map(([label, subjectValue, item, isRate]) => {
    const difference = isRate ? signed(item.absolute_difference * 100, " pts") : signed(item.absolute_difference);
    const mean = isRate ? percent(item.peer_mean) : formatNumber(item.peer_mean, 2);
    const subject = isRate ? percent(subjectValue) : formatNumber(subjectValue, 2);
    const percentage = item.percent_difference === null ? "mean is zero" : `${signed(item.percent_difference * 100, "%")} vs mean`;
    return comparisonRow(label, `${subject} vs mean ${mean} · ${difference} (${percentage})`, deltaClass(item.absolute_difference));
  }));
}

function renderPrevious(analysis) {
  const previous = analysis.previous_period;
  const host = document.querySelector("#previous-period");
  host.replaceChildren(
    comparisonRow("Previous benchmark rank", benchmarkRank(previous.period_benchmark_rank)),
    comparisonRow("Rank movement", previous.period_benchmark_rank_change === null ? "Not comparable" : signed(previous.period_benchmark_rank_change), deltaClass(previous.period_benchmark_rank_change)),
    comparisonRow(`Answers (previous: ${previous.answer_count})`, signed(previous.answer_count_change), deltaClass(previous.answer_count_change)),
    comparisonRow(`Score (previous: ${previous.total_answer_score})`, signed(previous.total_answer_score_change), deltaClass(previous.total_answer_score_change)),
    comparisonRow(`Acceptance (previous: ${percent(previous.acceptance_rate)})`, signed(previous.acceptance_rate_change * 100, " pts"), deltaClass(previous.acceptance_rate_change)),
  );
}

function renderLeaderboard(analysis) {
  const body = document.querySelector("#leaderboard-body");
  body.replaceChildren(...analysis.contributors.map((item) => {
    const row = document.createElement("tr");
    if (item.user_id === analysis.contributor.user_id) row.className = "subject";
    const values = [
      [item.period_benchmark_rank === null ? "—" : `#${item.period_benchmark_rank}`, "rank-number"],
      [decodeEntities(item.display_name), "contributor-name"],
      [item.official_all_time_rank ? `#${item.official_all_time_rank}` : "—", ""],
      [formatNumber(item.answer_count), ""],
      [formatNumber(item.total_answer_score), ""],
      [formatNumber(item.accepted_answer_count), ""],
      [formatNumber(item.average_answer_score, 2), ""],
    ];
    values.forEach(([value, className]) => row.append(element("td", className, value)));
    return row;
  }));
}

function renderAnalysis(analysis) {
  const contributor = analysis.contributor;
  document.querySelector("#results-heading").textContent = decodeEntities(contributor.display_name);
  document.querySelector("#result-context").textContent = `${analysis.tag} · ${analysis.period.start_date} to ${analysis.period.end_date} (exclusive end) · official cohort snapshot ${new Date(analysis.cohort.snapshot_at).toLocaleString()}`;
  const profileLink = document.querySelector("#profile-link");
  const profileUrl = safeProfileUrl(contributor.profile_url);
  profileLink.hidden = !profileUrl;
  if (profileUrl) profileLink.href = profileUrl;
  const activityNote = document.querySelector("#activity-note");
  activityNote.hidden = contributor.has_qualifying_answers;
  activityNote.textContent = contributor.has_qualifying_answers
    ? ""
    : `No qualifying ${analysis.tag} answers were found for this contributor during the selected month. Zero metrics are returned, and the contributor is not assigned a benchmark rank.`;
  document.querySelector("#cohort-note").textContent = analysis.cohort.subject_added_to_cohort
    ? "Official Top-20 + requested contributor"
    : "Official all-time Top-20";
  renderMetrics(analysis);
  renderPeers(analysis);
  renderPrevious(analysis);
  renderLeaderboard(analysis);
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderNarrative(narrative) {
  const fields = [
    ["Activity assessment", narrative.notable_contribution],
    ["Benchmark position", narrative.ranking_explanation],
    ["Compared with historical leaders", narrative.peer_comparison],
    ["Change from the previous month", narrative.period_change],
    ["Topic fingerprint", narrative.topic_fingerprint],
  ];
  if (narrative.root_cause_hypothesis) {
    fields.push(["Root-cause hypothesis", narrative.root_cause_hypothesis]);
  }
  const confidence = document.querySelector("#narrative-confidence");
  confidence.textContent = `${narrative.confidence} confidence`;
  confidence.hidden = false;
  const host = document.querySelector("#narrative-content");
  host.replaceChildren(...fields.map(([heading, value]) => {
    const block = element("article", "narrative-block");
    block.append(element("h3", "", heading), element("p", "", value));
    return block;
  }));
}

function resetNarrative() {
  document.querySelector("#narrative-content").replaceChildren();
  document.querySelector("#narrative-confidence").hidden = true;
  document.querySelector("#narrative-warning").hidden = true;
  document.querySelector("#narrative-loading").hidden = true;
}

function updateRawJson() {
  rawJson.textContent = JSON.stringify(displayedPayload, null, 2);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideStatus();
  resetNarrative();
  results.hidden = true;
  displayedPayload = {};
  submitButton.disabled = true;

  let userId;
  let tag;
  let range;
  try {
    userId = parseUserId(profileInput.value);
    tag = parseTag(tagInput.value);
    range = monthRange(monthInput.value);
  } catch (error) {
    showStatus(error.message, "error");
    submitButton.disabled = false;
    return;
  }

  const baseUrl = `/v1/tags/${encodeURIComponent(tag)}/contributors/${userId}`;
  const query = new URLSearchParams({ from_date: range.startDate, to_date: range.endDate });
  showStatus("Fetching and calculating deterministic contributor analytics…");

  let analysis;
  try {
    const response = await apiRequest(`${baseUrl}?${query}`);
    analysis = response.payload;
    displayedPayload.analysis = analysis;
    renderAnalysis(analysis);
    updateRawJson();
    showStatus(`Analytics completed successfully. Request ID: ${response.requestId || "not supplied"}`);
  } catch (error) {
    const request = error.requestId ? ` Request ID: ${error.requestId}.` : "";
    showStatus(`${error.message}${request}`, "error");
    submitButton.disabled = false;
    return;
  }

  const narrativeSection = document.querySelector("#narrative-section");
  narrativeSection.hidden = !narrativeInput.checked;
  if (narrativeInput.checked) {
    const loading = document.querySelector("#narrative-loading");
    loading.hidden = false;
    try {
      const response = await apiRequest(`${baseUrl}/narrative`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_date: range.startDate, end_date: range.endDate }),
      });
      displayedPayload.narrative = response.payload.narrative;
      renderNarrative(response.payload.narrative);
      updateRawJson();
    } catch (error) {
      const warning = document.querySelector("#narrative-warning");
      warning.textContent = `Analytics succeeded, but the AI narrative is unavailable: ${error.message}`;
      warning.hidden = false;
    } finally {
      loading.hidden = true;
    }
  }
  submitButton.disabled = false;
});

setDefaultMonth();
