type RelevanceStatus = {
  label: string;
  className: 'badgeHigh' | 'badgeMedium' | 'badgeLow' | 'badgeUnknown';
};

export function getRelevanceLevel(score?: number): 'high' | 'medium' | 'low' | undefined {
  if (score === undefined) return undefined;
  if (score >= 20) return 'high';
  if (score >= 10) return 'medium';
  return 'low';
}

export function getRelevanceStatus(score?: number): RelevanceStatus {
  const level = getRelevanceLevel(score);
  if (level === undefined) {
    return {
      label: 'Релевантность не определена',
      className: 'badgeUnknown',
    };
  }

  if (level === 'high') {
    return {
      label: 'Высокая релевантность',
      className: 'badgeHigh',
    };
  }

  if (level === 'low') {
    return {
      label: 'Низкая релевантность',
      className: 'badgeLow',
    };
  }

  return {
    label: 'Средняя релевантность',
    className: 'badgeMedium',
  };
}
