// The public landing page. Everything it describes is implemented in this app; no customers, logos or numbers
// are invented.
import Icon from "./Icon.jsx";

const FEATURES = [
  ["knowledge", "Shared project knowledge", "Upload PDFs, Word, Markdown, CSV and Excel files, public GitHub repositories and documentation pages once. Everyone on the project can ask about them."],
  ["quote", "Answers with citations", "Every answer names the document, page or file it came from, and sentences the sources do not support are removed."],
  ["builder", "Visual agent builder", "Draw an agent: guardrails, a router, your sources, a calculator and read-only tools, connected with lines."],
  ["lock", "Local-only privacy", "Mark a project local-only and its questions and documents never leave your machine: answers come from Qwen running locally."],
  ["chat", "English, తెలుగు, हिन्दी", "Ask in Telugu or Hindi about English documents and get the answer in your language."],
  ["user", "Companies and projects", "Invite colleagues, give each project its own members, and keep every company's knowledge separate."],
];

export default function Landing({ themeButton }) {
  return (
    <div className="landing">
      <header className="landing-bar">
        <span className="brand-inline"><span className="brand-dot" /> BharatAgent Studio</span>
        <span className="row-end">
          {themeButton}
          <a className="btn ghost" href="#/login">Sign in</a>
          <a className="btn primary" href="#/register">Get started</a>
        </span>
      </header>
      <section className="hero">
        <p className="eyebrow">AI for Bharat · open models and local privacy</p>
        <h1>Your team&apos;s knowledge.<br />One intelligent workspace.</h1>
        <p className="lead">Turn project documents, code repositories and internal knowledge into secure, multilingual AI
          assistants that your team can access whenever they need them.</p>
        <div className="row-center">
          <a className="btn primary lg" href="#/register">Get started, it&apos;s free</a>
          <a className="btn lg" href="#/login">Sign in</a>
        </div>
        <ol className="flow-strip" aria-label="How it works">
          <li><Icon name="upload" size={16} /> A colleague uploads project knowledge once</li>
          <li><Icon name="builder" size={16} /> The team lead builds a shared agent</li>
          <li><Icon name="chat" size={16} /> Everyone on the project asks and gets cited answers</li>
        </ol>
      </section>
      <section className="feature-grid" aria-label="Features">
        {FEATURES.map(([icon, title, text]) => (
          <article key={title} className="feature card">
            <span className="chip-icon group-main"><Icon name={icon} size={17} /></span>
            <h3>{title}</h3>
            <p className="muted">{text}</p>
          </article>
        ))}
      </section>
      <section className="models card" aria-label="Models">
        <h3>Choose the model for each question</h3>
        <p className="muted">Qwen 3 14B and Qwen 2.5 3B run on your own computer through Ollama; GPT-OSS 120B (Groq) and
          Gemini 2.5 Flash are available when your project allows cloud models. Every answer shows which model really
          answered, its tokens and its estimated cost.</p>
      </section>
      <footer className="landing-foot muted small">BharatAgent Studio · runs on your own machine with Docker · no tracking, no web fonts</footer>
    </div>
  );
}
