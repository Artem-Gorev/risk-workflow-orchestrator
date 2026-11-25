const shortWords = /(?<![\p{L}\p{M}\p{N}\p{Pd}_])(а|без|бы|в|во|да|для|до|же|за|и|из|из-за|из-под|к|ко|ли|на|над|не|ни|но|о|об|обо|от|по|под|при|про|с|со|у) +(?=[\p{L}\p{N}«„"(])/giu;

export function typograph(value) {
  return typeof value === 'string' ? value.replaceAll('\u00b7', '—').replace(shortWords, '$1\u00a0') : value;
}

export function shortCopy(value) {
  return typeof value === 'string' ? typograph(value.replace(/(?<!\.)\.(\s*)$/u, '$1')) : value;
}
