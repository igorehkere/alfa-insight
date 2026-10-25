type RelevanceStatus = {
  label: string;
  className: 'badgeHigh' | 'badgeMedium' | 'badgeUnknown';
};

export function getRelevanceStatus(score?: number): RelevanceStatus {
  if (score === undefined) {
    return {
      label: 'Релевантность не определена',
      className: 'badgeUnknown',
    };
  }

  if (score >= 20) {
    return {
      label: 'Высокая релевантность',
      className: 'badgeHigh',
    };
  }

  return {
    label: 'Средняя релевантность',
    className: 'badgeMedium',
  };
}