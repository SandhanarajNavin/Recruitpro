"use client";

import { useId, useState } from "react";
import { IconEye, IconEyeOff } from "@/components/Icons";

/**
 * Password field with a show/hide toggle.
 *
 * Shared by login and signup so the two forms cannot drift apart. Each instance
 * owns its own visibility state — on the signup form, revealing "Password" must
 * not also reveal "Confirm password", or the confirmation stops confirming
 * anything.
 */
export function PasswordInput({
  id,
  label,
  value,
  onChange,
  autoComplete,
  minLength,
  required = true,
}: {
  id?: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  minLength?: number;
  required?: boolean;
}) {
  const generated = useId();
  const inputId = id ?? generated;
  const [visible, setVisible] = useState(false);

  return (
    <>
      <label className="field-label" htmlFor={inputId}>
        {label}
      </label>
      <div className="password-field">
        <input
          id={inputId}
          type={visible ? "text" : "password"}
          value={value}
          autoComplete={autoComplete}
          minLength={minLength}
          onChange={(event) => onChange(event.target.value)}
          required={required}
        />
        <button
          type="button"
          className="password-toggle"
          // Not in the tab order: keyboard users tabbing from password to submit
          // should not land on a decorative control. Still reachable by click and
          // announced to screen readers.
          tabIndex={-1}
          onClick={() => setVisible((shown) => !shown)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          title={visible ? "Hide password" : "Show password"}
        >
          {visible ? <IconEyeOff size={18} /> : <IconEye size={18} />}
        </button>
      </div>
    </>
  );
}
