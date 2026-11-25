import { Children, cloneElement, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { shortCopy, typograph } from './typography.js';
import './select-control.css';

const menuLimit = 260;
const menuGap = 6;
const viewportGutter = 10;

function readOptions(children) {
  return Children.toArray(children)
    .filter((child) => isValidElement(child) && child.type === 'option')
    .map(({ props }) => {
      const label = String(props.label ?? Children.toArray(props.children).join(''));
      return {
        label,
        value: String(props.value ?? label),
        disabled: Boolean(props.disabled),
      };
    });
}

function fitMenu(trigger, menu) {
  const viewport = window.visualViewport;
  const viewportTop = viewport?.offsetTop ?? 0;
  const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
  const rect = trigger.getBoundingClientRect();
  const below = viewportBottom - rect.bottom - menuGap - viewportGutter;
  const above = rect.top - viewportTop - menuGap - viewportGutter;
  const opensAbove = below < Math.min(menu.scrollHeight, menuLimit) && above > below;
  return { opensAbove, height: Math.max(44, Math.min(menuLimit, opensAbove ? above : below)) };
}

export function SelectControl({ control, id, labelId, describedBy }) {
  const fallbackId = useId();
  const triggerId = id ?? fallbackId;
  const menuId = triggerId + '-options';
  const errorId = triggerId + '-validation';
  const container = useRef(null);
  const trigger = useRef(null);
  const native = useRef(null);
  const menu = useRef(null);
  const typing = useRef({ text: '', time: 0 });
  const [open, setOpen] = useState(false);
  const [activeValue, setActiveValue] = useState(undefined);
  const [position, setPosition] = useState({ opensAbove: false, height: menuLimit });
  const [validation, setValidation] = useState(null);
  const { disabled, required, value, defaultValue, children } = control.props;
  const [uncontrolledValue, setUncontrolledValue] = useState(defaultValue);
  const options = readOptions(children);
  const enabled = options.filter((option) => !option.disabled);
  const selected = options.find((option) => option.value === String(value ?? uncontrolledValue)) ?? enabled[0] ?? options[0];
  const expanded = open && !disabled && options.length > 0;
  const active = enabled.find((option) => option.value === activeValue) ?? enabled.find((option) => option.value === selected?.value) ?? enabled[0];
  const activeIndex = options.indexOf(active);
  const invalid = !disabled && validation?.value === (selected?.value ?? '');

  function close() {
    setOpen(false);
    typing.current = { text: '', time: 0 };
  }

  function show(option = enabled.find((item) => item.value === selected?.value) ?? enabled[0]) {
    if (disabled || !options.length) return;
    setActiveValue(option?.value);
    setOpen(true);
  }

  function choose(option) {
    if (disabled || !option || option.disabled) return;
    const select = native.current;
    if (select.value !== option.value) {
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (value == null) setUncontrolledValue(option.value);
    if (option.value || !required) setValidation(null);
    close();
  }

  function move(delta) {
    const current = enabled.indexOf(active);
    const next = Math.min(enabled.length - 1, Math.max(0, current + delta));
    setActiveValue(enabled[next]?.value);
  }

  function typeahead(character) {
    const now = Date.now();
    const before = now - typing.current.time < 650 ? typing.current.text : '';
    const text = before + character.toLocaleLowerCase();
    typing.current = { text, time: now };
    const repeated = [...text].every((letter) => letter === text[0]);
    const query = repeated ? text[0] : text;
    const current = enabled.indexOf(expanded ? active : selected);
    const start = Math.max(0, current + (repeated ? 1 : 0));
    const order = [...enabled.slice(start), ...enabled.slice(0, start)];
    const next = order.find((option) => option.label.toLocaleLowerCase().startsWith(query));
    if (next) show(next);
  }

  function onKeyDown(event) {
    if (event.key === 'Tab') {
      if (expanded) {
        if (active) choose(active);
        else close();
      }
      return;
    }
    if (event.key === 'Escape') {
      if (expanded) {
        event.preventDefault();
        event.stopPropagation();
        close();
      }
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const down = event.key === 'ArrowDown';
      if (expanded) move(down ? 1 : -1);
      else show(enabled.find((option) => option.value === selected?.value) ?? (down ? enabled[0] : enabled.at(-1)));
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      show(event.key === 'Home' ? enabled[0] : enabled.at(-1));
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (expanded) {
        if (active) choose(active);
        else close();
      } else show();
      return;
    }
    if (event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      typeahead(event.key);
    }
  }

  useEffect(() => {
    if (!expanded) return;
    function onPointerDown(event) {
      if (!container.current?.contains(event.target)) close();
    }
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [expanded]);

  useEffect(() => {
    if (disabled) close();
  }, [disabled]);

  useLayoutEffect(() => {
    if (!expanded) return;
    function place(event) {
      if (event?.target === menu.current) return;
      const next = fitMenu(trigger.current, menu.current);
      setPosition((prev) => prev.opensAbove === next.opensAbove && prev.height === next.height ? prev : next);
    }
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    window.visualViewport?.addEventListener('resize', place);
    window.visualViewport?.addEventListener('scroll', place);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      window.visualViewport?.removeEventListener('resize', place);
      window.visualViewport?.removeEventListener('scroll', place);
    };
  }, [expanded, options.length]);

  useLayoutEffect(() => {
    if (!expanded || activeIndex < 0) return;
    const list = menu.current;
    const item = list.children[activeIndex];
    const min = item.offsetTop;
    const max = min + item.offsetHeight;
    if (min < list.scrollTop) list.scrollTop = min;
    if (max > list.scrollTop + list.clientHeight) list.scrollTop = max - list.clientHeight;
  }, [expanded, activeIndex, position.height]);

  function onInvalid(event) {
    event.preventDefault();
    const select = event.currentTarget;
    setValidation({ value: select.value, message: select.validity.valueMissing ? 'Выберите значение.' : select.validationMessage });
    control.props.onInvalid?.(event);
    const first = select.form?.querySelector('input:invalid, select:invalid, textarea:invalid');
    if (!first || first === select) trigger.current?.focus();
  }

  const description = [describedBy, invalid && errorId].filter(Boolean).join(' ') || undefined;
  return (
    <div className="select-field">
      <div className="select-box" ref={container}>
        {cloneElement(control, { ref: native, id: undefined, tabIndex: -1, className: 'select-native', 'aria-hidden': true, onInvalid })}
        <button
          ref={trigger} id={triggerId} className="select-trigger" type="button" role="combobox"
          aria-labelledby={labelId} aria-describedby={description} aria-haspopup="listbox" aria-expanded={expanded}
          aria-controls={expanded ? menuId : undefined}
          aria-activedescendant={expanded && activeIndex >= 0 ? menuId + '-' + activeIndex : undefined}
          aria-invalid={invalid || undefined} aria-required={required || undefined} disabled={disabled}
          onClick={() => {
            trigger.current.focus({ preventScroll: true });
            if (expanded) close();
            else show();
          }}
          onKeyDown={onKeyDown}
          onBlur={(event) => {
            if (event.relatedTarget && !container.current?.contains(event.relatedTarget)) close();
          }}
        >
          <span className="select-value">{typograph(selected?.label ?? 'Нет доступных вариантов')}</span>
          <span className="select-arrow" aria-hidden="true" />
        </button>
        {expanded && (
          <div
            ref={menu} id={menuId} className="select-options" role="listbox" aria-labelledby={labelId}
            data-side={position.opensAbove ? 'above' : 'below'} style={{ maxHeight: position.height }}
          >
            {options.map((option, index) => (
              <div
                key={index} id={menuId + '-' + index} role="option" className="select-option"
                aria-selected={option === selected} aria-disabled={option.disabled || undefined}
                data-active={index === activeIndex || undefined}
                onMouseDown={(event) => event.preventDefault()}
                onMouseMove={() => { if (!option.disabled) setActiveValue(option.value); }}
                onClick={() => choose(option)}
              >
                {typograph(option.label)}
              </div>
            ))}
          </div>
        )}
      </div>
      {invalid && <p id={errorId} className="select-validation" role="alert">{shortCopy(validation.message)}</p>}
    </div>
  );
}
