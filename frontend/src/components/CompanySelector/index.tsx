import { useEffect, useState, type FormEvent } from 'react';
import css from './index.module.scss';

interface Company {
  company_id: string;
  industry_name: string | null;
  region: string | null;
  is_ambiguous: boolean;
}

interface CompanyResponse {
  items: Company[];
}

interface CompanySelectorProps {
  onBack: () => void;
  onContinue: (companyId: string, role: string) => void;
}

export const CompanySelector = ({ onBack, onContinue }: CompanySelectorProps) => {
  const [companies, setCompanies] = useState<Company[]>([]);

  const [selectedCompanyId, setSelectedCompanyId] = useState('');
  const [role, setRole] = useState('');

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();

    const fetchCompanies = async () => {
      try {
        const response = await fetch('/api/companies?limit=10', { signal: controller.signal });

        if (!response.ok) {
          throw new Error('Не удалось загрузить компании');
        }

        const data: CompanyResponse = await response.json();

        setCompanies(data.items);
      } catch {
        if (!controller.signal.aborted) {
          setError('Ошибка загрузки компаний');
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    };

    fetchCompanies();

    return () => controller.abort();
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!selectedCompanyId || !role) {
      return;
    }

    onContinue(selectedCompanyId, role);
  };

  return (
    <form className={css.form} onSubmit={handleSubmit}>
      <div className={css.field}>
        <label htmlFor="company">Ваша компания</label>

        <select
          id="company"
          className={css.select}
          value={selectedCompanyId}
          onChange={(e) => setSelectedCompanyId(e.target.value)}
          disabled={loading || companies.length === 0}
          required
        >
          <option value="">{loading ? 'Загрузка компаний...' : 'Выберите компанию'}</option>

          {companies.map((company) => (
            <option key={company.company_id} value={company.company_id}>
              {company.company_id}
            </option>
          ))}
        </select>
      </div>

      <div className={css.field}>
        <label htmlFor="role">Ваша роль</label>

        <select id="role" className={css.select} value={role} onChange={(e) => setRole(e.target.value)} required>
          <option value="">Выберите роль</option>
          <option value="owner">Владелец бизнеса</option>
          <option value="director">Руководитель</option>
          <option value="finance">Финансовый директор</option>
          <option value="analyst">Аналитик</option>
        </select>
      </div>

      {error && <p className={css.error}>{error}</p>}

      <div className={css.buttons}>
        <button type="button" onClick={onBack}>
          Назад
        </button>

        <button type="submit" disabled={!selectedCompanyId || !role || loading}>
          Продолжить
        </button>
      </div>
    </form>
  );
};
