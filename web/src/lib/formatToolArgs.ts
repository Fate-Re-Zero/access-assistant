const DEFAULT_VALUE_MAX_LENGTH = 100;
const DEFAULT_SUMMARY_MAX_LENGTH = 180;

export function formatArgValue(
  value: unknown,
  maxLength = DEFAULT_VALUE_MAX_LENGTH,
): string {
  if (value === null) {
    return "null";
  }
  if (value === undefined) {
    return "undefined";
  }

  if (typeof value === "string") {
    return value.length > maxLength
      ? `${value.slice(0, maxLength - 3)}...`
      : value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  let serialized = "";
  try {
    serialized = JSON.stringify(value);
  } catch {
    return String(value);
  }

  return serialized.length > maxLength
    ? `${serialized.slice(0, maxLength - 3)}...`
    : serialized;
}

function flattenArgEntries(args: Record<string, unknown>): Array<[string, unknown]> {
  const entries = Object.entries(args);
  if (entries.length !== 1) {
    return entries;
  }

  const [key, value] = entries[0];
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return entries;
  }

  const nestedEntries = Object.entries(value as Record<string, unknown>);
  if (nestedEntries.length === 0) {
    return entries;
  }

  // MCP tools often wrap fields in a single object arg such as `request`.
  if (key === "request" || key === "input" || key === "params") {
    return nestedEntries;
  }

  return entries;
}

export function formatToolArgs(
  args: Record<string, unknown>,
  maxLength = DEFAULT_SUMMARY_MAX_LENGTH,
): string {
  const summarized = flattenArgEntries(args)
    .map(([key, value]) => `${key}=${formatArgValue(value)}`)
    .join(", ");

  if (!summarized) {
    return "";
  }

  return summarized.length > maxLength
    ? `${summarized.slice(0, maxLength - 3)}...`
    : summarized;
}
