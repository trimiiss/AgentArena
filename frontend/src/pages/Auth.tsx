import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AuthLayout } from "../components/Layout";
import { Button, ErrorNotice, Field, Input } from "../components/ui";

export function LoginPage() {
  const { session, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (session) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/");
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Sign in" subtitle="Test and benchmark your AI agents.">
      <form onSubmit={submit} className="space-y-3">
        {error !== null && <ErrorNotice error={error} />}
        <Field label="Email">
          <Input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </Field>
        <Field label="Password">
          <Input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        <Button type="submit" variant="primary" loading={busy} className="w-full">
          Sign in
        </Button>
      </form>
      <p className="mt-4 text-center text-sm text-ink-2">
        New here?{" "}
        <Link to="/register" className="font-medium text-accent-ink hover:underline">
          Create an account
        </Link>
      </p>
    </AuthLayout>
  );
}

export function RegisterPage() {
  const { session, register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ display_name: "", organization_name: "", email: "", password: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (session) return <Navigate to="/" replace />;

  const update = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(form);
      navigate("/");
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Create your workspace" subtitle="You become the owner of a new organization.">
      <form onSubmit={submit} className="space-y-3">
        {error !== null && <ErrorNotice error={error} />}
        <Field label="Your name">
          <Input required autoComplete="name" value={form.display_name} onChange={update("display_name")} />
        </Field>
        <Field label="Organization">
          <Input required minLength={2} autoComplete="organization" value={form.organization_name} onChange={update("organization_name")} />
        </Field>
        <Field label="Work email">
          <Input type="email" required autoComplete="email" value={form.email} onChange={update("email")} />
        </Field>
        <Field label="Password" hint="At least 10 characters.">
          <Input type="password" required minLength={10} autoComplete="new-password" value={form.password} onChange={update("password")} />
        </Field>
        <Button type="submit" variant="primary" loading={busy} className="w-full">
          Create account
        </Button>
      </form>
      <p className="mt-4 text-center text-sm text-ink-2">
        Already registered?{" "}
        <Link to="/login" className="font-medium text-accent-ink hover:underline">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
