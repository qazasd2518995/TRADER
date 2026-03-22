const TAIPEI_UTC_OFFSET_HOURS = 8;
const TAIPEI_DATE_TIME_FORMATTER = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const TAIPEI_DATE_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

type DateInputParts = {
  year: number;
  month: number;
  day: number;
};

export function getTaipeiDateInput(now = new Date()): string {
  return TAIPEI_DATE_FORMATTER.format(now);
}

export function addDaysToDateInput(value: string, days: number): string {
  const date = toDateInputAnchor(value);
  date.setUTCDate(date.getUTCDate() + days);
  return formatDateInputAnchor(date);
}

export function getMondayDateInput(value: string): string {
  const date = toDateInputAnchor(value);
  const weekday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - weekday);
  return formatDateInputAnchor(date);
}

export function startOfMonthDateInput(value: string, monthOffset = 0): string {
  const date = toDateInputAnchor(value);
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + monthOffset);
  return formatDateInputAnchor(date);
}

export function endOfMonthDateInput(value: string, monthOffset = 0): string {
  const date = toDateInputAnchor(value);
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + monthOffset + 1);
  date.setUTCDate(0);
  return formatDateInputAnchor(date);
}

export function getTaipeiDateRangeUnix(value: string): { start: number; end: number } {
  const { year, month, day } = parseDateInput(value);
  const start = Math.floor(Date.UTC(year, month - 1, day, -TAIPEI_UTC_OFFSET_HOURS, 0, 0) / 1000);
  return { start, end: start + 86399 };
}

export function normalizeMt5Timestamp(unixSeconds: number | undefined, skewSeconds = 0): number {
  if (!unixSeconds || unixSeconds <= 0) {
    return 0;
  }
  return unixSeconds + skewSeconds;
}

export function formatTaipeiTradeTime(unixSeconds: number | undefined, skewSeconds = 0): string {
  const normalized = normalizeMt5Timestamp(unixSeconds, skewSeconds);
  if (normalized <= 0) {
    return "";
  }

  const parts = TAIPEI_DATE_TIME_FORMATTER.formatToParts(new Date(normalized * 1000));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.month}/${values.day} ${values.hour}:${values.minute}`;
}

function parseDateInput(value: string): DateInputParts {
  const [year, month, day] = value.split("-").map((part) => Number(part));
  return { year, month, day };
}

function toDateInputAnchor(value: string): Date {
  const { year, month, day } = parseDateInput(value);
  return new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
}

function formatDateInputAnchor(value: Date): string {
  const year = value.getUTCFullYear();
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  const day = String(value.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
