// Adapt display-only tab labels while preserving values used in routing/state.
export * from 'komodo-shared-ui';
import {
  MobileFriendlyTabs as OriginalTabs,
  MobileFriendlyTabsSelector as OriginalSelector,
  StatusBadge as OriginalStatusBadge,
  type MobileFriendlyTabsProps,
  type MobileFriendlyTabsSelectorProps,
  type ColorIntention,
} from 'komodo-shared-ui';
import { t } from './runtime';
import type { TextProps } from '@mantine/core';

export const StatusBadge = ((props: TextProps & {text: string | undefined; intent: ColorIntention}) => {
  return <OriginalStatusBadge {...props} text={props.text ? t(props.text) : props.text} />;
}) as typeof OriginalStatusBadge;

export function MobileFriendlyTabs(props: MobileFriendlyTabsProps) {
  return <OriginalTabs {...props} tabs={props.tabs.map(tab => ({
    ...tab, label: tab.label ?? t(tab.value),
  }))} />;
}
export function MobileFriendlyTabsSelector(props: MobileFriendlyTabsSelectorProps) {
  return <OriginalSelector {...props} tabs={props.tabs.map(tab => ({
    ...tab, label: tab.label ?? t(tab.value),
  }))} />;
}
