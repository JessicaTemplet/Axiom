import { PILLARS, RECAP_PARTS, type PillarIcon } from '../data/content.ts'
import { DOCS } from '../data/site.ts'
import { SectionHead } from '../components/SectionHead.tsx'
import { ArrowUpRight, Bot, Box, LinkIcon, Loop, Shield, Terminal } from '../components/Icons.tsx'
import { inline } from '../lib/inline.tsx'

const ICONS: Record<PillarIcon, typeof Box> = {
  box: Box,
  shield: Shield,
  terminal: Terminal,
  bot: Bot,
  loop: Loop,
  link: LinkIcon,
}

export function Pillars() {
  return (
    <section className="section section--alt" id="why" aria-labelledby="why-h">
      <div className="container">
        <SectionHead id="why-h" eyebrow="Why Axiom" title="A small language. Serious capabilities.">
          <p>The expressiveness of functional programming, with a direct path to native code. Every design choice gives you something concrete to work with.</p>
        </SectionHead>

        <ul className="pillars">
          {PILLARS.map((p, i) => {
            const Icon = ICONS[p.icon]
            return (
              <li className="pillar" key={p.title}>
                <span className="pillar__number" aria-hidden>{String(i + 1).padStart(2, '0')}</span>
                <span className="pillar__icon" aria-hidden>
                  <Icon size={20} />
                </span>
                <h3>{p.title}</h3>
                <p>{inline(p.body)}</p>
                <a className="pillar__proof" href={p.href} target="_blank" rel="noreferrer noopener">
                  <code>{p.proof}</code>
                  <ArrowUpRight size={12} />
                </a>
              </li>
            )
          })}
        </ul>

        <article className="recap" id="recap" aria-labelledby="recap-h">
          <div className="recap__intro">
            <p className="recap__eyebrow">Axiom’s memory model</p>
            <h3 id="recap-h">RECAP</h3>
            <p>
              Automatic ownership for everyday values. Checked regions when you need a bounded
              allocation lifetime. Reference counting and arenas work together, with no tracing
              collector or borrow checker.
            </p>
            <div className="recap__links">
              <a href={`${DOCS}/reference.md#what-recap-guarantees`} target="_blank" rel="noreferrer noopener">
                Learn the model <ArrowUpRight size={14} />
              </a>
              <a href={`${DOCS}/reference.md#regions`} target="_blank" rel="noreferrer noopener">
                Use checked regions <ArrowUpRight size={14} />
              </a>
            </div>
          </div>
          <dl className="recap__parts">
            {RECAP_PARTS.map((part) => (
              <div key={part.letter}>
                <dt><span aria-hidden="true">{part.letter}</span>{part.term}</dt>
                <dd>{inline(part.text)}</dd>
              </div>
            ))}
          </dl>
          <div className="recap__limits">
            <p>
              Know the boundaries: cycles and larger dead blocks wait for an arena reset.
              Some ownership paths still retain shares. Raw-address validity remains an unsafe
              obligation; effect checks do not prove it.
            </p>
            <a href={`${DOCS}/memory-model.md`} target="_blank" rel="noreferrer noopener">
              Read the guarantees and limits <ArrowUpRight size={14} />
            </a>
          </div>
        </article>
      </div>
    </section>
  )
}
