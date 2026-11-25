import { useLayoutEffect, useRef, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { shortCopy } from './typography.js';
import './date-control.css';

const earliest = '0001-01-01';
const latest = '9999-12-31';
const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const monthNames = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];
const dayLabel = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'full', timeZone: 'UTC' });
const todayLabel = new Intl.DateTimeFormat('sv-SE', {
  year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'Europe/Moscow',
});

function dateParts(value) {
  return value.split('-').map(Number);
}

function asDate(year, month, day) {
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  return date;
}

function isoDate(year, month, day) {
  const pad = number => String(number).padStart(2, '0');
  return `${String(year).padStart(4, '0')}-${pad(month)}-${pad(day)}`;
}

function displayDate(value) {
  return value ? value.split('-').reverse().join('.') : '';
}

function parseDate(text) {
  if (typeof text !== 'string') return null;
  const trimmed = text.trim();
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed);
  const local = /^(\d{1,2})[./](\d{1,2})[./](\d{4})$/.exec(trimmed) ?? /^(\d{2})(\d{2})(\d{4})$/.exec(trimmed);
  if (!iso && !local) return null;
  const [year, month, day] = (iso ? iso.slice(1) : [local[3], local[2], local[1]]).map(Number);
  const date = asDate(year, month, day);
  if (year < 1 || date.getUTCFullYear() !== year || date.getUTCMonth() + 1 !== month || date.getUTCDate() !== day) return null;
  return isoDate(year, month, day);
}

function shiftDays(value, amount) {
  const [year, month, day] = dateParts(value);
  const date = asDate(year, month, day + amount);
  const shiftedYear = date.getUTCFullYear();
  if (shiftedYear < 1 || shiftedYear > 9999) return null;
  return isoDate(shiftedYear, date.getUTCMonth() + 1, date.getUTCDate());
}

function shiftMonths(value, amount) {
  const [year, month, day] = dateParts(value);
  const index = (year - 1) * 12 + month - 1 + amount;
  const shiftedYear = Math.floor(index / 12) + 1;
  if (shiftedYear < 1 || shiftedYear > 9999) return null;
  const shiftedMonth = index % 12 + 1;
  const monthEnd = asDate(shiftedYear, shiftedMonth + 1, 0).getUTCDate();
  return isoDate(shiftedYear, shiftedMonth, Math.min(day, monthEnd));
}

function weekday(value) {
  return (asDate(...dateParts(value)).getUTCDay() + 6) % 7;
}

function boundedDate(date, min, max) {
  if (date < min) return min;
  if (date > max) return max;
  return date;
}

function validationMessage(text, date, min, max, required) {
  if (!text) return required ? 'Укажите дату.' : '';
  if (!date) return 'Введите дату в\u00a0формате ДД.ММ.ГГГГ.';
  if (date < min) return `Дата не\u00a0может быть раньше ${displayDate(min)}.`;
  if (date > max) return `Дата не\u00a0может быть позже ${displayDate(max)}.`;
  return '';
}

function placeCalendar(anchor, panel) {
  const spacing = 12;
  const gap = 8;
  const box = anchor.getBoundingClientRect();
  const topEdge = (window.visualViewport?.offsetTop ?? 0) + spacing;
  const bottomEdge = Math.min(window.innerHeight, topEdge - spacing + (window.visualViewport?.height ?? window.innerHeight)) - spacing;
  panel.style.maxHeight = `${Math.max(0, bottomEdge - topEdge)}px`;
  const width = panel.offsetWidth;
  const height = panel.offsetHeight;
  const rightEdge = document.documentElement.clientWidth - spacing;
  const left = Math.max(spacing, Math.min(box.left, rightEdge - width));
  const preferredTop = box.bottom + gap + height <= bottomEdge ? box.bottom + gap : box.top - height - gap;
  const top = Math.max(topEdge, Math.min(preferredTop, bottomEdge - height));
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
}

export function DateControl({ control, id, labelId, describedBy }) {
  const {
    value = '', min: minValue, max: maxValue, onChange, name, required = false,
    disabled = false, readOnly = false, autoFocus, placeholder, onBlur, onFocus,
  } = control.props;
  const min = parseDate(minValue ?? '') ?? earliest;
  const max = parseDate(maxValue ?? '') ?? latest;
  const selected = parseDate(value);
  const today = todayLabel.format(new Date());
  const [edit, setEdit] = useState(null);
  const [focusDate, setCursor] = useState(null);
  const [showError, setShowError] = useState(false);
  const frame = useRef(null);
  const input = useRef(null);
  const popup = useRef(null);
  const returnTo = useRef(null);
  const focusDay = useRef(false);
  const text = edit?.source === value ? edit.text : displayDate(selected);
  const date = parseDate(text);
  const error = validationMessage(text, date, min, max, required);
  const cursor = focusDate ? boundedDate(focusDate, min, max) : null;
  const open = Boolean(cursor) && !disabled && !readOnly;
  const visibleError = showError && error && !disabled && !readOnly;
  const popupId = `${id}-calendar`;
  const monthId = `${popupId}-month`;
  const errorId = `${id}-error`;
  const start = cursor ? cursor.slice(0, 7) + '-01' : null;
  const before = start ? shiftMonths(start, -1) : null;
  const after = start ? shiftMonths(start, 1) : null;
  const canGoBack = before && min.slice(0, 7) < cursor.slice(0, 7);
  const canGoAhead = after && max.slice(0, 7) > cursor.slice(0, 7);
  const firstWeekday = start ? weekday(start) : 0;
  const weeks = start ? Array.from({ length: 6 }, (_, week) =>
    dayNames.map((_, day) => shiftDays(start, week * 7 + day - firstWeekday)),
  ) : [];

  useLayoutEffect(() => {
    // Required empty text uses the browser's required constraint. Range and format use the same text as the inline error.
    input.current.setCustomValidity(text && !disabled && !readOnly ? error : '');
  }, [text, error, disabled, readOnly]);

  useLayoutEffect(() => {
    if (!open) return;
    const update = () => placeCalendar(frame.current, popup.current);
    const closeOutside = event => {
      if (!frame.current.contains(event.target)) setCursor(null);
    };
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    window.visualViewport?.addEventListener('resize', update);
    window.visualViewport?.addEventListener('scroll', update);
    document.addEventListener('pointerdown', closeOutside, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      window.visualViewport?.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('scroll', update);
      document.removeEventListener('pointerdown', closeOutside, true);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    placeCalendar(frame.current, popup.current);
    if (focusDay.current || document.activeElement?.disabled) {
      popup.current.querySelector('[data-date-active]')?.focus({ preventScroll: true });
      focusDay.current = false;
    }
  }, [cursor, open]);

  function emit(valueToSend) {
    if (valueToSend === value) return;
    const target = { id, name, value: valueToSend };
    onChange?.({ target, currentTarget: target });
  }

  function editText(event) {
    const nextText = event.target.value;
    const nextDate = parseDate(nextText);
    const nextError = validationMessage(nextText, nextDate, min, max, required);
    event.target.setCustomValidity(nextText ? nextError : '');
    const canonical = !nextText ? '' : nextError ? value : nextDate;
    setEdit({ source: canonical, text: nextText });
    emit(canonical);
    if (open && !nextError && nextDate) setCursor(nextDate);
  }

  function showCalendar(anchor) {
    if (disabled || readOnly) return;
    returnTo.current = anchor;
    focusDay.current = true;
    setCursor(boundedDate(selected ?? today, min, max));
  }

  function closeCalendar(restoreFocus = false) {
    setCursor(null);
    if (restoreFocus) returnTo.current?.focus({ preventScroll: true });
  }

  function choose(valueToSend) {
    const formatted = displayDate(valueToSend);
    input.current.setCustomValidity('');
    setEdit({ source: valueToSend, text: formatted });
    setShowError(false);
    emit(valueToSend);
    closeCalendar(true);
  }

  function moveMonth(amount) {
    const next = shiftMonths(cursor, amount);
    if (next) setCursor(boundedDate(next, min, max));
  }

  function moveDay(event) {
    const weekDay = weekday(cursor);
    const days = {
      ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7,
      Home: -weekDay, End: 6 - weekDay,
    };
    let next;
    if (event.key === 'PageUp' || event.key === 'PageDown') {
      const amount = event.key === 'PageUp' ? -1 : 1;
      next = shiftMonths(cursor, amount * (event.shiftKey ? 12 : 1)) ?? (amount < 0 ? earliest : latest);
    } else if (Object.hasOwn(days, event.key)) {
      next = shiftDays(cursor, days[event.key]) ?? (days[event.key] < 0 ? earliest : latest);
    } else {
      return;
    }
    event.preventDefault();
    focusDay.current = true;
    const bounded = boundedDate(next, min, max);
    if (bounded === cursor) focusDay.current = false;
    else setCursor(bounded);
  }

  function handleKey(event) {
    if (event.key === 'Escape' && open) {
      event.preventDefault();
      event.stopPropagation();
      closeCalendar(true);
    }
    if (event.key === 'ArrowDown' && event.altKey && event.target === input.current && !open) {
      event.preventDefault();
      showCalendar(input.current);
    }
  }

  const buttonLabel = open ? 'Закрыть календарь' : 'Открыть календарь';
  const dayInRange = day => day && day >= min && day <= max;

  return (
    <div className="date-control" ref={frame} onKeyDown={handleKey} onBlur={event => {
      if (!frame.current.contains(event.relatedTarget)) {
        setShowError(true);
        if (open && event.relatedTarget) closeCalendar();
      }
    }}>
      <div className="date-input-row">
        <input
          id={id}
          ref={input}
          className="date-input"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          placeholder={placeholder ?? 'ДД.ММ.ГГГГ'}
          value={text}
          required={required}
          disabled={disabled}
          readOnly={readOnly}
          autoFocus={autoFocus}
          aria-labelledby={labelId}
          aria-describedby={[describedBy, visibleError ? errorId : null].filter(Boolean).join(' ') || undefined}
          aria-invalid={visibleError ? true : undefined}
          onChange={editText}
          onFocus={onFocus}
          onInvalid={() => setShowError(true)}
          onBlur={event => {
            if (date && !error) setEdit({ source: value, text: displayDate(date) });
            onBlur?.(event);
          }}
        />
        <button
          className="date-toggle"
          type="button"
          tabIndex={disabled || readOnly ? -1 : undefined}
          disabled={disabled || readOnly}
          aria-label={buttonLabel}
          title={buttonLabel}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? popupId : undefined}
          onClick={event => open ? closeCalendar() : showCalendar(event.currentTarget)}
        >
          <CalendarDays size={17} aria-hidden="true" />
        </button>
      </div>
      {name && <input type="hidden" name={name} disabled={disabled} value={selected ?? ''} />}
      {visibleError && <p className="date-error" id={errorId} role="alert">{shortCopy(error)}</p>}
      {open && (
        <div className="date-popup" id={popupId} ref={popup} role="dialog" aria-label="Календарь">
          <div className="date-month-nav">
            <button type="button" aria-label="Предыдущий месяц" disabled={!canGoBack} onClick={() => moveMonth(-1)}>
              <ChevronLeft size={17} aria-hidden="true" />
            </button>
            <div className="date-month-label" id={monthId} aria-live="polite">
              {monthNames[Number(cursor.slice(5, 7)) - 1]}&nbsp;{cursor.slice(0, 4)}
            </div>
            <button type="button" aria-label="Следующий месяц" disabled={!canGoAhead} onClick={() => moveMonth(1)}>
              <ChevronRight size={17} aria-hidden="true" />
            </button>
          </div>
          <div className="date-grid" role="grid" aria-labelledby={monthId}>
            <div className="date-week date-weekdays" role="row">
              {dayNames.map(day => <span key={day} role="columnheader">{day}</span>)}
            </div>
            {weeks.map((week, index) => (
              <div className="date-week" role="row" key={index}>
                {week.map((day, dayIndex) => (
                  <div role="gridcell" key={day ?? dayIndex} aria-selected={day ? day === selected : undefined}>
                    {day && (
                      <button
                        className="date-day"
                        type="button"
                        disabled={!dayInRange(day)}
                        tabIndex={day === cursor ? 0 : -1}
                        aria-label={dayLabel.format(asDate(...dateParts(day)))}
                        aria-current={day === today ? 'date' : undefined}
                        data-date-active={day === cursor ? '' : undefined}
                        data-outside={day.slice(0, 7) !== cursor.slice(0, 7) ? '' : undefined}
                        onKeyDown={moveDay}
                        onClick={() => choose(day)}
                      >
                        {Number(day.slice(8))}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
          {((!required && text) || dayInRange(today)) && (
            <div className="date-popup-actions">
              {!required && text && <button type="button" onClick={() => choose('')}>Очистить</button>}
              {dayInRange(today) && <button type="button" onClick={() => choose(today)}>Сегодня</button>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
