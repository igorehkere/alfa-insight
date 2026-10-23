import cn from 'classnames';
import css from './index.module.scss';

type props = {
  type: 'page' | 'section';
};

export const Loader = ({ type }: props) => {
  return (
    <span
      className={cn({
        [css.loader]: true,
        [css[`type-${type}`]]: true,
      })}
    />
  );
};